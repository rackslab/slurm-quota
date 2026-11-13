"""Unit tests for slurm_quota.reallocate."""

from __future__ import annotations

from unittest.mock import patch

from slurm_quota.database import (
    init_database,
    query_accounts_aggregate,
    query_users_aggregate,
)
from slurm_quota.migrate import CONSUMPTION_MIGRATED_AT, UNATTRIBUTED
from slurm_quota.reallocate import (
    SOURCE_ALL,
    SOURCE_ASSOCIATIONS,
    SOURCE_SACCT,
    SOURCE_SACCT_OTHER,
    ReallocationError,
    build_weights,
    fit_consumption,
    reallocate_unattributed,
)
from tests.test_support import SlurmQuotaTestCase


def _sums(distribution, index):
    sums: dict[str, int] = {}
    for pair, value in distribution.items():
        sums[pair[index]] = sums.get(pair[index], 0) + value
    return sums


def _full(users, accounts):
    return {(u, a): 1.0 for u in users for a in accounts}


class TestFitConsumption(SlurmQuotaTestCase):
    def test_empty(self):
        self.assertEqual(fit_consumption({}, {}, {}), ({}, {}))
        self.assertEqual(fit_consumption({"u1": 0}, {"a1": 0}, {}), ({}, {}))

    def test_single_user_and_account(self):
        self.assertEqual(
            fit_consumption({"u1": 42}, {"a1": 42}, {("u1", "a1"): 1.0}),
            ({("u1", "a1"): 42}, {}),
        )

    def test_full_uniform_support_is_proportional(self):
        users = {"u1": 300, "u2": 100}
        accounts = {"a1": 200, "a2": 200, "a3": 0}
        result, deviations = fit_consumption(users, accounts, _full(users, accounts))
        self.assertEqual(
            result,
            {
                ("u1", "a1"): 150,
                ("u1", "a2"): 150,
                ("u2", "a1"): 50,
                ("u2", "a2"): 50,
            },
        )
        self.assertEqual(deviations, {})

    def test_exact_totals_with_rounding(self):
        users = {"u1": 7, "u2": 5, "u3": 1, "u4": 0}
        accounts = {"a1": 3, "a2": 3, "a3": 3, "a4": 4}
        result, deviations = fit_consumption(users, accounts, _full(users, accounts))
        self.assertTrue(all(value > 0 for value in result.values()))
        self.assertEqual(_sums(result, 0), {k: v for k, v in users.items() if v})
        self.assertEqual(_sums(result, 1), accounts)
        self.assertEqual(deviations, {})

    def test_restricted_support_follows_weights(self):
        # u1 only on a1, u2 on a1 and a2 with a 1:3 split in Slurm accounting
        users = {"u1": 100, "u2": 400}
        accounts = {"a1": 200, "a2": 300}
        weights = {("u1", "a1"): 10.0, ("u2", "a1"): 25.0, ("u2", "a2"): 75.0}
        result, deviations = fit_consumption(users, accounts, weights)
        self.assertEqual(
            result,
            {("u1", "a1"): 100, ("u2", "a1"): 100, ("u2", "a2"): 300},
        )
        self.assertEqual(deviations, {})

    def test_shares_are_kept_when_consistent_with_totals(self):
        users = {"u1": 60, "u2": 40}
        accounts = {"a1": 50, "a2": 50}
        weights = {
            ("u1", "a1"): 30.0,
            ("u1", "a2"): 30.0,
            ("u2", "a1"): 20.0,
            ("u2", "a2"): 20.0,
        }
        result, _ = fit_consumption(users, accounts, weights)
        self.assertEqual(
            result,
            {
                ("u1", "a1"): 30,
                ("u1", "a2"): 30,
                ("u2", "a1"): 20,
                ("u2", "a2"): 20,
            },
        )

    def test_chains_of_accounts_are_balanced(self):
        # a1 and a3 have no common user: rounding is fixed through a2.
        users = {"u1": 1, "u2": 1, "u3": 1}
        accounts = {"a1": 1, "a2": 1, "a3": 1}
        weights = {
            ("u1", "a1"): 1.0,
            ("u1", "a2"): 1.0,
            ("u2", "a2"): 1.0,
            ("u2", "a3"): 1.0,
            ("u3", "a3"): 1.0,
            ("u3", "a1"): 1.0,
        }
        result, deviations = fit_consumption(users, accounts, weights)
        self.assertEqual(deviations, {})
        self.assertEqual(_sums(result, 0), users)
        self.assertEqual(_sums(result, 1), accounts)

    def test_infeasible_support_keeps_user_totals(self):
        # u1 only on a1 but a1 total is lower than u1 total
        users = {"u1": 100, "u2": 50}
        accounts = {"a1": 60, "a2": 90}
        weights = {("u1", "a1"): 1.0, ("u2", "a1"): 1.0, ("u2", "a2"): 1.0}
        result, deviations = fit_consumption(users, accounts, weights)
        self.assertEqual(_sums(result, 0), users)
        self.assertEqual(deviations, {"a1": 40, "a2": -40})

    def test_different_overall_totals_keep_user_totals(self):
        users = {"u1": 60, "u2": 40}
        accounts = {"a1": 150, "a2": 50}
        with self.assertLogs("slurm_quota", level="WARNING") as log_cm:
            result, deviations = fit_consumption(
                users, accounts, _full(users, accounts)
            )
        self.assertEqual(_sums(result, 0), users)
        self.assertEqual(_sums(result, 1), {"a1": 75, "a2": 25})
        self.assertEqual(deviations, {})
        self.assertIn(
            "WARNING:slurm_quota:Account a1 consumption rescaled: 150 -> 75 seconds",
            log_cm.output,
        )

    def test_only_users_constrained(self):
        result, deviations = fit_consumption(
            {"u1": 10},
            {"a1": 0},
            {("u1", "a1"): 1.0, ("u1", "a2"): 4.0},
        )
        self.assertEqual(result, {("u1", "a1"): 2, ("u1", "a2"): 8})
        self.assertEqual(deviations, {})

    def test_only_accounts_constrained(self):
        result, _ = fit_consumption(
            {}, {"a1": 9}, {("u1", "a1"): 2.0, ("u2", "a1"): 1.0}
        )
        self.assertEqual(result, {("u1", "a1"): 6, ("u2", "a1"): 3})

    def test_missing_pair_raises(self):
        with self.assertRaisesRegex(
            ReallocationError, "No user/account pair for user u2, account a2"
        ):
            fit_consumption({"u1": 1, "u2": 1}, {"a1": 1, "a2": 1}, {("u1", "a1"): 1.0})

    def test_deterministic(self):
        users = {f"u{i}": i * 37 % 101 for i in range(20)}
        total = sum(users.values())
        accounts = {"a1": total // 3, "a2": total // 7}
        accounts["a3"] = total - accounts["a1"] - accounts["a2"]
        weights = {(u, a): float(len(u) + len(a)) for u in users for a in accounts}
        first = fit_consumption(users, accounts, weights)
        second = fit_consumption(
            dict(reversed(list(users.items()))),
            dict(reversed(list(accounts.items()))),
            dict(reversed(list(weights.items()))),
        )
        self.assertEqual(first, second)
        self.assertEqual(_sums(first[0], 1), accounts)


class TestBuildWeights(SlurmQuotaTestCase):
    def test_sources_by_order_of_preference(self):
        users = {"u1": 10, "u2": 10, "u3": 10, "u4": 10}
        accounts = {"a1": 20, "a2": 20}
        usage = {("u1", "a1"): 100, ("u1", "x"): 5}
        other_usage = {("u2", "a2"): 50}
        associations = {("u3", "a1"), ("u3", "a2"), ("u1", "a2")}
        weights, sources = build_weights(
            users, accounts, usage, other_usage, associations
        )
        self.assertEqual(
            weights,
            {
                ("u1", "a1"): 100.0,
                ("u2", "a2"): 50.0,
                ("u3", "a1"): 1.0,
                ("u3", "a2"): 1.0,
                ("u4", "a1"): 1.0,
                ("u4", "a2"): 1.0,
            },
        )
        self.assertEqual(sources[SOURCE_SACCT], ["user u1"])
        self.assertEqual(sources[SOURCE_SACCT_OTHER], ["user u2"])
        self.assertEqual(sources[SOURCE_ASSOCIATIONS], ["user u3"])
        self.assertEqual(sources[SOURCE_ALL], ["user u4"])

    def test_accounts_without_pairs_get_pairs(self):
        weights, sources = build_weights(
            {"u1": 10},
            {"a1": 5, "a2": 5},
            {("u1", "a1"): 10},
            {},
            {("u1", "a2")},
        )
        self.assertEqual(weights, {("u1", "a1"): 10.0, ("u1", "a2"): 1.0})
        self.assertEqual(sources[SOURCE_ASSOCIATIONS], ["account a2"])

    def test_counterparts_unrestricted_when_side_has_no_total(self):
        weights, _ = build_weights(
            {"u1": 10}, {}, {("u1", "a1"): 3, ("u1", "a2"): 1}, {}, set()
        )
        self.assertEqual(weights, {("u1", "a1"): 3.0, ("u1", "a2"): 1.0})

    def test_unattributed_pairs_are_ignored(self):
        weights, _ = build_weights(
            {"u1": 10},
            {},
            {("u1", UNATTRIBUTED): 3, ("u1", "a1"): 1},
            {},
            set(),
        )
        self.assertEqual(weights, {("u1", "a1"): 1.0})


class TestReallocateUnattributed(SlurmQuotaTestCase):
    def setUp(self):
        super().setUp()
        init_database()
        with self.db_connection() as conn:
            # Legacy: u1 120 min CPU, u2 60 min CPU + 10 min GPU,
            # a1 90 min CPU + 10 min GPU, a2 90 min CPU.
            self.insert_consumption(conn, "u1", UNATTRIBUTED, 120)
            self.insert_consumption(conn, "u2", UNATTRIBUTED, 60, 10)
            self.insert_consumption(conn, UNATTRIBUTED, "a1", 90, 10)
            self.insert_consumption(conn, UNATTRIBUTED, "a2", 90)
            # Consumption charged after migration on actual pairs
            self.insert_consumption(conn, "u1", "a1", 5)
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, '1700000000')",
                (CONSUMPTION_MIGRATED_AT,),
            )
            conn.commit()

    def _totals(self):
        with self.db_connection() as conn:
            users = {
                u["username"]: (
                    u["total_consumed_cpu_minutes"],
                    u["total_consumed_gpu_minutes"],
                )
                for u in query_users_aggregate(conn)
            }
            accounts = {
                a["account"]: (
                    a["total_consumed_cpu_minutes"],
                    a["total_consumed_gpu_minutes"],
                )
                for a in query_accounts_aggregate(conn)
            }
        return users, accounts

    def _pairs(self):
        with self.db_connection() as conn:
            return conn.execute(
                "SELECT username, account, consumed_cpu_seconds, "
                "consumed_gpu_seconds FROM user_account_consumption "
                "ORDER BY username, account"
            ).fetchall()

    def test_reallocates_and_removes_unattributed(self):
        users_before, accounts_before = self._totals()
        usage = {
            ("u1", "a1"): (3000, 0),
            ("u1", "a2"): (9000, 0),
            ("u2", "a1"): (6000, 600),
            ("other", "a3"): (100, 0),
        }
        report = reallocate_unattributed(usage, set())
        users_after, accounts_after = self._totals()

        # Totals are preserved, unattributed entities are removed
        users_before.pop(UNATTRIBUTED)
        accounts_before.pop(UNATTRIBUTED)
        self.assertEqual(users_after, users_before)
        self.assertEqual(accounts_after, accounts_before)
        self.assertEqual(
            self._pairs(),
            [
                ("u1", "a1", 5 * 60 + 30 * 60, 0),
                ("u1", "a2", 90 * 60, 0),
                ("u2", "a1", 60 * 60, 10 * 60),
            ],
        )
        self.assertEqual(report.pairs, 3)
        self.assertEqual(report.unattributed_cpu_seconds, 180 * 60)
        self.assertEqual(report.unattributed_gpu_seconds, 10 * 60)
        self.assertEqual(report.sources["cpu"][SOURCE_SACCT], ["user u1", "user u2"])
        self.assertEqual(report.deviations, {"cpu": {}, "gpu": {}})

    def test_dry_run_does_not_modify_database(self):
        before = self._pairs()
        report = reallocate_unattributed(
            {("u1", "a1"): (1, 0), ("u2", "a2"): (1, 1)},
            set(),
            dry_run=True,
        )
        self.assertEqual(self._pairs(), before)
        # u2 GPU consumption can only go to a1, the only account with GPU
        # consumption.
        self.assertEqual(report.pairs, 3)

    def test_creates_missing_counterparts_with_default_quotas(self):
        with self.db_connection() as conn:
            conn.execute(
                "DELETE FROM user_account_consumption WHERE username = ?",
                (UNATTRIBUTED,),
            )
            conn.execute(
                "UPDATE settings SET value = '77' "
                "WHERE key = 'default_account_quota_cpu_minutes'"
            )
            conn.commit()
        reallocate_unattributed(
            {("u1", "new"): (60, 0), ("u2", "new"): (60, 60)}, set()
        )
        with self.db_connection() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT quota_cpu_minutes FROM accounts WHERE account = 'new'"
                ).fetchone(),
                (77,),
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM users WHERE username = ?", (UNATTRIBUTED,)
                ).fetchone()
            )

    def test_unplaceable_consumption_raises_and_rolls_back(self):
        with self.db_connection() as conn:
            conn.execute(
                "DELETE FROM user_account_consumption WHERE username = ?",
                (UNATTRIBUTED,),
            )
            conn.commit()
        before = self._pairs()
        # u1 has neither Slurm accounting data, nor association and no account
        # has unattributed consumption.
        with self.assertRaisesRegex(
            ReallocationError, "CPU consumption: No user/account pair for user u1"
        ):
            reallocate_unattributed({("u2", "a1"): (60, 60)}, set())
        self.assertEqual(self._pairs(), before)

    def test_fallback_to_all_counterparts_is_logged(self):
        with self.assertLogs("slurm_quota", level="WARNING") as log_cm:
            reallocate_unattributed({("u1", "a1"): (60, 0)}, set())
        self.assertIn(
            "WARNING:slurm_quota:CPU consumption of user u2 reallocated to all "
            "counterparts, no Slurm accounting data or association found",
            log_cm.output,
        )

    def test_associations_are_used_without_accounting_data(self):
        with patch("slurm_quota.reallocate.logger"):
            reallocate_unattributed({}, {("u1", "a2"), ("u2", "a1"), ("u1", "a1")})
        pairs = {(user, account) for user, account, _, _ in self._pairs()}
        self.assertEqual(pairs, {("u1", "a1"), ("u1", "a2"), ("u2", "a1")})
