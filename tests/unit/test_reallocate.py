"""Unit tests for slurm_quota.reallocate."""

from __future__ import annotations

import argparse
import io
from contextlib import redirect_stdout
from unittest.mock import patch

from slurm_quota.database import (
    init_database,
    query_accounts_aggregate,
    query_users_aggregate,
)
from slurm_quota.migrate import UNATTRIBUTED
from slurm_quota.reallocate import (
    SOURCE_ASSOCIATIONS,
    SOURCE_NONE,
    SOURCE_SACCT,
    SOURCE_SACCT_OTHER,
    Deviation,
    FitResult,
    ReallocationError,
    ReallocationReport,
    _deviations,
    _log_deviations,
    _print_report,
    _user_list,
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
        self.assertEqual(fit_consumption({}, {}, {}), FitResult())
        self.assertEqual(fit_consumption({"u1": 0}, {"a1": 0}, {}), FitResult())

    def test_single_user_and_account(self):
        self.assertEqual(
            fit_consumption({"u1": 42}, {"a1": 42}, {("u1", "a1"): 1.0}),
            FitResult(pairs={("u1", "a1"): 42}, account_targets={"a1": 42}),
        )

    def test_full_uniform_support_is_proportional(self):
        users = {"u1": 300, "u2": 100}
        accounts = {"a1": 200, "a2": 200, "a3": 0}
        fit = fit_consumption(users, accounts, _full(users, accounts))
        self.assertEqual(
            fit.pairs,
            {
                ("u1", "a1"): 150,
                ("u1", "a2"): 150,
                ("u2", "a1"): 50,
                ("u2", "a2"): 50,
            },
        )
        self.assertEqual(fit.unreachable, {})

    def test_exact_totals_with_rounding(self):
        users = {"u1": 7, "u2": 5, "u3": 1, "u4": 0}
        accounts = {"a1": 3, "a2": 3, "a3": 3, "a4": 4}
        fit = fit_consumption(users, accounts, _full(users, accounts))
        self.assertTrue(all(value > 0 for value in fit.pairs.values()))
        self.assertEqual(_sums(fit.pairs, 0), {k: v for k, v in users.items() if v})
        self.assertEqual(_sums(fit.pairs, 1), accounts)
        self.assertEqual(fit.unreachable, {})

    def test_restricted_support_follows_weights(self):
        # u1 only on a1, u2 on a1 and a2 with a 1:3 split in Slurm accounting
        users = {"u1": 100, "u2": 400}
        accounts = {"a1": 200, "a2": 300}
        weights = {("u1", "a1"): 10.0, ("u2", "a1"): 25.0, ("u2", "a2"): 75.0}
        fit = fit_consumption(users, accounts, weights)
        self.assertEqual(
            fit.pairs,
            {("u1", "a1"): 100, ("u2", "a1"): 100, ("u2", "a2"): 300},
        )
        self.assertEqual(fit.unreachable, {})

    def test_shares_are_kept_when_consistent_with_totals(self):
        users = {"u1": 60, "u2": 40}
        accounts = {"a1": 50, "a2": 50}
        weights = {
            ("u1", "a1"): 30.0,
            ("u1", "a2"): 30.0,
            ("u2", "a1"): 20.0,
            ("u2", "a2"): 20.0,
        }
        fit = fit_consumption(users, accounts, weights)
        self.assertEqual(
            fit.pairs,
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
        fit = fit_consumption(users, accounts, weights)
        self.assertEqual(fit.unreachable, {})
        self.assertEqual(_sums(fit.pairs, 0), users)
        self.assertEqual(_sums(fit.pairs, 1), accounts)

    def test_infeasible_support_keeps_user_totals(self):
        # u1 only on a1 but a1 total is lower than u1 total
        users = {"u1": 100, "u2": 50}
        accounts = {"a1": 60, "a2": 90}
        weights = {("u1", "a1"): 1.0, ("u2", "a1"): 1.0, ("u2", "a2"): 1.0}
        fit = fit_consumption(users, accounts, weights)
        self.assertEqual(_sums(fit.pairs, 0), users)
        self.assertEqual(fit.unreachable, {"a1": 40, "a2": -40})

    def test_different_overall_totals_keep_user_totals(self):
        users = {"u1": 60, "u2": 40}
        accounts = {"a1": 150, "a2": 50}
        fit = fit_consumption(users, accounts, _full(users, accounts))
        self.assertEqual(_sums(fit.pairs, 0), users)
        self.assertEqual(_sums(fit.pairs, 1), {"a1": 75, "a2": 25})
        self.assertEqual(fit.account_targets, {"a1": 75, "a2": 25})
        self.assertEqual(fit.unreachable, {})

    def test_only_users_constrained(self):
        fit = fit_consumption(
            {"u1": 10},
            {"a1": 0},
            {("u1", "a1"): 1.0, ("u1", "a2"): 4.0},
        )
        self.assertEqual(fit.pairs, {("u1", "a1"): 2, ("u1", "a2"): 8})
        self.assertEqual(fit.unreachable, {})

    def test_only_accounts_constrained(self):
        fit = fit_consumption({}, {"a1": 9}, {("u1", "a1"): 2.0, ("u2", "a1"): 1.0})
        self.assertEqual(fit.pairs, {("u1", "a1"): 6, ("u2", "a1"): 3})

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
        self.assertEqual(_sums(first.pairs, 1), accounts)


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
            },
        )
        self.assertEqual(sources[SOURCE_SACCT], ["user u1"])
        self.assertEqual(sources[SOURCE_SACCT_OTHER], ["user u2"])
        self.assertEqual(sources[SOURCE_ASSOCIATIONS], ["user u3"])
        # u4 has no pair from any source
        self.assertEqual(sources[SOURCE_NONE], ["user u4"])

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
            conn.commit()

    def _totals(self):
        with self.db_connection() as conn:
            users = {
                u["username"]: (
                    u["total_consumed_cpu_seconds"],
                    u["total_consumed_gpu_seconds"],
                )
                for u in query_users_aggregate(conn)
            }
            accounts = {
                a["account"]: (
                    a["total_consumed_cpu_seconds"],
                    a["total_consumed_gpu_seconds"],
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
        self.assertEqual(report.reallocated_cpu_seconds, 180 * 60)
        self.assertEqual(report.reallocated_gpu_seconds, 10 * 60)
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
        # u2 GPU jobs are only on a2, an account without GPU consumption, so u2
        # GPU consumption is dropped.
        self.assertEqual(report.pairs, 2)

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

    def test_unpaired_consumption_is_left_unattributed(self):
        # Only u1/a1 found in Slurm accounting, no association: u2 and a2 have no
        # pair for CPU, u2 and a1 have no pair for GPU.
        with self.assertLogs("slurm_quota", level="WARNING") as log_cm:
            report = reallocate_unattributed({("u1", "a1"): (60, 0)}, set())
        unpaired = (
            "no job in Slurm accounting in the selected period nor Slurm "
            "association found, its consumption is left unattributed"
        )
        # a1 is the only paired account, it gets the users total.
        rescaled = (
            "rescaled as overall unattributed consumption of users and accounts differ"
        )
        self.assertEqual(
            log_cm.output,
            [
                "WARNING:slurm_quota:CPU overall unattributed consumption of users "
                "(120 minutes) differs from the one of accounts (90 minutes) by +30 "
                "minutes, account totals cannot all be preserved",
                "WARNING:slurm_quota:CPU consumption of user u2: 0 minutes "
                "reallocated instead of 60 minutes of unattributed consumption "
                "(-60), " + unpaired,
                "WARNING:slurm_quota:CPU consumption of account a2: 0 minutes "
                "reallocated instead of 90 minutes of unattributed consumption "
                "(-90), " + unpaired,
                "WARNING:slurm_quota:CPU consumption of account a1: 120 minutes "
                "reallocated instead of 90 minutes of unattributed consumption "
                "(+30), " + rescaled,
                "WARNING:slurm_quota:GPU consumption of user u2: 0 minutes "
                "reallocated instead of 10 minutes of unattributed consumption "
                "(-10), " + unpaired,
                "WARNING:slurm_quota:GPU consumption of account a1: 0 minutes "
                "reallocated instead of 10 minutes of unattributed consumption "
                "(-10), " + unpaired,
            ],
        )
        self.assertEqual(report.reallocated_cpu_seconds, 7200)
        self.assertEqual(report.reallocated_gpu_seconds, 0)
        self.assertEqual(report.unpaired("cpu"), ["user u2", "account a2"])
        self.assertEqual(report.unpaired("gpu"), ["user u2", "account a1"])
        self.assertEqual(
            report.deviations["cpu"]["user u2"], Deviation(3600, 0, [unpaired])
        )
        # Consumption of unpaired users and accounts is left unattributed
        self.assertEqual(
            self._pairs(),
            [
                (UNATTRIBUTED, "a1", 0, 600),
                (UNATTRIBUTED, "a2", 5400, 0),
                ("u1", "a1", 5 * 60 + 7200, 0),
                ("u2", UNATTRIBUTED, 3600, 600),
            ],
        )

        # A later run with Slurm associations reallocates what is left, then
        # removes unattributed user and account.
        with patch("slurm_quota.reallocate.logger"):
            reallocate_unattributed({}, {("u2", "a1"), ("u2", "a2")})
        self.assertEqual(
            self._pairs(),
            [
                ("u1", "a1", 5 * 60 + 7200, 0),
                ("u2", "a1", 0, 600),
                ("u2", "a2", 3600, 0),
            ],
        )
        with self.db_connection() as conn:
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM users WHERE username = ?", (UNATTRIBUTED,)
                ).fetchone()
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM accounts WHERE account = ?", (UNATTRIBUTED,)
                ).fetchone()
            )

    def test_associations_are_used_without_accounting_data(self):
        with patch("slurm_quota.reallocate.logger"):
            reallocate_unattributed({}, {("u1", "a2"), ("u2", "a1"), ("u1", "a1")})
        pairs = {(user, account) for user, account, _, _ in self._pairs()}
        self.assertEqual(pairs, {("u1", "a1"), ("u1", "a2"), ("u2", "a1")})

    def test_rescaled_accounts_are_reported_with_reason(self):
        with self.db_connection() as conn:
            # Accounts sum to 210 minutes, users to 180 minutes.
            self.insert_consumption(conn, UNATTRIBUTED, "a2", 30)
            conn.commit()
        usage = {
            ("u1", "a1"): (60, 0),
            ("u1", "a2"): (60, 0),
            ("u2", "a1"): (60, 60),
            ("u2", "a2"): (60, 0),
        }
        with self.assertLogs("slurm_quota", level="WARNING") as log_cm:
            report = reallocate_unattributed(usage, set())
        reason = (
            "rescaled as overall unattributed consumption of users and accounts differ"
        )
        self.assertEqual(
            log_cm.output,
            [
                "WARNING:slurm_quota:CPU overall unattributed consumption of users "
                "(180 minutes) differs from the one of accounts (210 minutes) by "
                "-30 minutes, account totals cannot all be preserved",
                "WARNING:slurm_quota:CPU consumption of account a2: 103 minutes "
                "reallocated instead of 120 minutes of unattributed consumption "
                "(-17), " + reason,
                "WARNING:slurm_quota:CPU consumption of account a1: 77 minutes "
                "reallocated instead of 90 minutes of unattributed consumption "
                "(-13), " + reason,
            ],
        )
        self.assertEqual(
            report.deviations["cpu"],
            {
                "account a2": Deviation(7200, 6171, [reason]),
                "account a1": Deviation(5400, 4629, [reason]),
            },
        )
        self.assertEqual(report.deviations["gpu"], {})

    def test_unreachable_accounts_are_reported_with_reason(self):
        # u1 (120 min) only on a1 (90 min), u2 (60 min) only on a2 (90 min)
        usage = {("u1", "a1"): (60, 0), ("u2", "a2"): (60, 0)}
        with self.assertLogs("slurm_quota", level="WARNING") as log_cm:
            report = reallocate_unattributed(usage, set())
        surplus = (
            "users only paired with this account have more unattributed "
            "consumption than this account: u1 (120 minutes)"
        )
        deficit = "users paired with this account do not allow to reach its total"
        self.assertEqual(
            log_cm.output[:2],
            [
                "WARNING:slurm_quota:CPU consumption of account a1: 120 minutes "
                "reallocated instead of 90 minutes of unattributed consumption "
                "(+30), " + surplus,
                "WARNING:slurm_quota:CPU consumption of account a2: 60 minutes "
                "reallocated instead of 90 minutes of unattributed consumption "
                "(-30), " + deficit,
            ],
        )
        self.assertEqual(
            report.deviations["cpu"],
            {
                "account a1": Deviation(5400, 7200, [surplus], exclusive_users=["u1"]),
                "account a2": Deviation(5400, 3600, [deficit]),
            },
        )
        # User totals are preserved
        self.assertNotIn("user u1", report.deviations["cpu"])
        self.assertEqual(report.notable("cpu"), ["account a1", "account a2"])
        self.assertEqual(report.over_quota("cpu"), [])

    def _single_account_gap(self):
        """Replace legacy consumption: u3 (1000 min) only consumed on a1 (10 min),
        u1 (600 min) and u2 (400 min) on a2 (600 min) and a3 (400 min)."""
        with self.db_connection() as conn:
            conn.execute("DELETE FROM user_account_consumption")
            self.insert_consumption(conn, "u1", UNATTRIBUTED, 600)
            self.insert_consumption(conn, "u2", UNATTRIBUTED, 400)
            self.insert_consumption(conn, "u3", UNATTRIBUTED, 1000)
            self.insert_consumption(conn, UNATTRIBUTED, "a1", 10)
            self.insert_consumption(conn, UNATTRIBUTED, "a2", 600)
            self.insert_consumption(conn, UNATTRIBUTED, "a3", 400)
            conn.commit()
        # In Slurm accounting, u3 only consumed 20 minutes.
        return {
            ("u1", "a2"): (360 * 60, 0),
            ("u1", "a3"): (240 * 60, 0),
            ("u2", "a2"): (240 * 60, 0),
            ("u2", "a3"): (160 * 60, 0),
            ("u3", "a1"): (20 * 60, 0),
        }

    def test_gap_on_single_account_is_reported_with_its_users(self):
        usage = self._single_account_gap()
        with self.assertLogs("slurm_quota", level="WARNING") as log_cm:
            report = reallocate_unattributed(usage, set(), dry_run=True)
        # The difference between users and accounts goes to a1, through u3 only
        # paired with it. Other accounts keep their unattributed consumption and
        # are not reported, although they do not reach their rescaled totals.
        self.assertEqual(
            log_cm.output,
            [
                "WARNING:slurm_quota:CPU overall unattributed consumption of users "
                "(2000 minutes) differs from the one of accounts (1010 minutes) by "
                "+990 minutes, account totals cannot all be preserved",
                "WARNING:slurm_quota:CPU consumption of account a1: 1000 minutes "
                "reallocated instead of 10 minutes of unattributed consumption "
                "(+990), users only paired with this account have more unattributed "
                "consumption than this account: u3 (1000 minutes)",
            ],
        )
        self.assertEqual(report.notable("cpu"), ["account a1"])

    def test_accounts_exceeding_quota_are_reported(self):
        usage = self._single_account_gap()
        with self.db_connection() as conn:
            conn.execute(
                "UPDATE accounts SET quota_cpu_minutes = 500 WHERE account = 'a1'"
            )
            # a2 is already over quota before the reallocation, not reported.
            conn.execute(
                "UPDATE accounts SET quota_cpu_minutes = 100 WHERE account = 'a2'"
            )
            conn.commit()
        with self.assertLogs("slurm_quota", level="WARNING") as log_cm:
            report = reallocate_unattributed(usage, set(), dry_run=True)
        self.assertEqual(
            log_cm.output[1],
            "WARNING:slurm_quota:CPU consumption of account a1: 1000 minutes "
            "reallocated instead of 10 minutes of unattributed consumption (+990), "
            "users only paired with this account have more unattributed consumption "
            "than this account: u3 (1000 minutes); exceeds its CPU quota of 500 "
            "minutes with 1000 minutes, jobs on this account will be refused",
        )
        self.assertEqual(report.over_quota("cpu"), ["account a1"])
        with redirect_stdout(io.StringIO()) as out:
            _print_report(report, dry_run=True)
        self.assertIn(
            "CPU users and accounts not matching their unattributed consumption: 1 "
            "(see warnings)",
            out.getvalue(),
        )
        self.assertIn(
            "CPU accounts exceeding their quota after reallocation: 1 (see warnings)",
            out.getvalue(),
        )


class TestDeviations(SlurmQuotaTestCase):
    def test_negligible_differences_are_reported_together(self):
        # Rounding moved 50 seconds from a2 to a1, within 0.1% of their totals,
        # while their rescaled totals are not reached.
        fit = FitResult(
            pairs={("u1", "a1"): 100050, ("u1", "a2"): 199950},
            account_targets={"a1": 110000, "a2": 190000},
        )
        deviations = _deviations(
            "cpu",
            {"u1": 300000},
            {"a1": 100000, "a2": 200000},
            fit,
            set(),
            {},
            -1,
            {},
            {("u1", "a1"): 1.0, ("u1", "a2"): 1.0},
        )
        self.assertEqual(
            deviations,
            {
                "account a1": Deviation(100000, 100050, [], negligible=True),
                "account a2": Deviation(200000, 199950, [], negligible=True),
            },
        )
        report = ReallocationReport(deviations={"cpu": deviations})
        self.assertEqual(report.notable("cpu"), [])
        with self.assertLogs("slurm_quota", level="WARNING") as log_cm:
            _log_deviations("cpu", deviations)
        self.assertEqual(
            log_cm.output,
            [
                "WARNING:slurm_quota:CPU consumption of 2 account(s) differs from "
                "their unattributed consumption by less than 0.1% (1 minutes at "
                "most), due to the rounding of the distribution",
            ],
        )

    def test_users_list_is_truncated(self):
        users = {f"u{index}": index * 60 for index in range(1, 8)}
        fit = FitResult(
            pairs={(name, "a1"): value for name, value in users.items()},
            account_targets={"a1": 60},
        )
        deviations = _deviations(
            "cpu",
            users,
            {"a1": 60},
            fit,
            set(),
            {},
            -1,
            {},
            {(name, "a1"): 1.0 for name in users},
        )
        self.assertEqual(
            deviations["account a1"].reasons,
            [
                "users only paired with this account have more unattributed "
                "consumption than this account: u7 (7 minutes), u6 (6 minutes), "
                "u5 (5 minutes), u4 (4 minutes), u3 (3 minutes) and 2 more"
            ],
        )


class TestCorrectUsers(SlurmQuotaTestCase):
    """Corrections of unattributed consumption of users with Slurm accounting.

    Legacy: u3 (1000 min) only consumed on a1 (10 min), u1 (600 min) and u2
    (400 min) on a2 (600 min) and a3 (400 min).
    """

    CORRECTED = (
        "WARNING:slurm_quota:CPU consumption of user u3 corrected to 20 minutes "
        "instead of 1000 minutes of unattributed consumption (-980), its "
        "consumption in Slurm accounting in the selected period, as users only "
        "paired with account a1 have more unattributed consumption than this "
        "account"
    )

    def setUp(self):
        super().setUp()
        init_database()
        with self.db_connection() as conn:
            self.insert_consumption(conn, "u1", UNATTRIBUTED, 600)
            self.insert_consumption(conn, "u2", UNATTRIBUTED, 400)
            self.insert_consumption(conn, "u3", UNATTRIBUTED, 1000)
            self.insert_consumption(conn, UNATTRIBUTED, "a1", 10)
            self.insert_consumption(conn, UNATTRIBUTED, "a2", 600)
            self.insert_consumption(conn, UNATTRIBUTED, "a3", 400)
            conn.commit()
        self.usage = {
            ("u1", "a2"): (360 * 60, 0),
            ("u1", "a3"): (240 * 60, 0),
            ("u2", "a2"): (240 * 60, 0),
            ("u2", "a3"): (160 * 60, 0),
            ("u3", "a1"): (20 * 60, 0),
        }

    def _pairs(self):
        with self.db_connection() as conn:
            return conn.execute(
                "SELECT username, account, consumed_cpu_seconds "
                "FROM user_account_consumption ORDER BY username, account"
            ).fetchall()

    def test_dry_run_reports_corrections(self):
        before = self._pairs()
        with self.assertLogs("slurm_quota", level="WARNING") as log_cm:
            report = reallocate_unattributed(
                self.usage, set(), dry_run=True, correct_users=True
            )
        self.assertEqual(
            log_cm.output,
            [
                "WARNING:slurm_quota:CPU overall unattributed consumption of users "
                "(2000 minutes) differs from the one of accounts (1010 minutes) by "
                "+990 minutes, account totals cannot all be preserved",
                self.CORRECTED,
                "WARNING:slurm_quota:CPU overall unattributed consumption of users "
                "after correction (1020 minutes) differs from the one of accounts "
                "(1010 minutes) by +10 minutes, account totals cannot all be "
                "preserved",
                "WARNING:slurm_quota:CPU consumption of account a1: 20 minutes "
                "reallocated instead of 10 minutes of unattributed consumption "
                "(+10), users only paired with this account have more unattributed "
                "consumption than this account: u3 (20 minutes)",
            ],
        )
        self.assertEqual(self._pairs(), before)
        self.assertEqual(report.corrected("cpu"), ["u3"])
        self.assertEqual(report.reallocated_cpu_seconds, 1020 * 60)
        with redirect_stdout(io.StringIO()) as out:
            _print_report(report, dry_run=True)
        self.assertIn(
            "CPU users corrected from Slurm accounting: 1 (see warnings)",
            out.getvalue(),
        )

    def test_corrections_are_applied(self):
        with patch("slurm_quota.reallocate.logger"):
            reallocate_unattributed(self.usage, set(), correct_users=True)
        pairs = self._pairs()
        self.assertIn(("u3", "a1", 20 * 60), pairs)
        self.assertNotIn(UNATTRIBUTED, {user for user, _, _ in pairs})
        self.assertNotIn(UNATTRIBUTED, {account for _, account, _ in pairs})
        with self.db_connection() as conn:
            self.assertIsNone(
                conn.execute(
                    "SELECT comment FROM users WHERE username = 'u3'"
                ).fetchone()[0]
            )

    def test_corrections_are_suggested_without_option(self):
        with self.assertLogs("slurm_quota", level="WARNING") as log_cm:
            report = reallocate_unattributed(self.usage, set(), dry_run=True)
        self.assertNotIn(self.CORRECTED, log_cm.output)
        self.assertEqual(report.corrected("cpu"), ["u3"])
        self.assertEqual(report.reallocated_cpu_seconds, 2000 * 60)
        with redirect_stdout(io.StringIO()) as out:
            _print_report(report, dry_run=True)
        self.assertIn(
            "CPU users that --correct-users would correct: 1 (see warnings)",
            out.getvalue(),
        )

    def test_consumption_lower_than_accounting_is_not_corrected(self):
        self.usage[("u3", "a1")] = (2000 * 60, 0)
        with self.assertLogs("slurm_quota", level="WARNING") as log_cm:
            report = reallocate_unattributed(
                self.usage, set(), dry_run=True, correct_users=True
            )
        self.assertIn(
            "WARNING:slurm_quota:CPU consumption of user u3 not corrected, its "
            "unattributed consumption (1000 minutes) does not exceed its "
            "consumption in Slurm accounting in the selected period (2000 "
            "minutes)",
            log_cm.output,
        )
        self.assertEqual(report.corrected("cpu"), [])
        self.assertEqual(report.reallocated_cpu_seconds, 2000 * 60)

    def test_users_without_accounting_are_not_corrected(self):
        # u3 is paired with a1 from Slurm associations only.
        del self.usage[("u3", "a1")]
        with self.assertLogs("slurm_quota", level="WARNING") as log_cm:
            report = reallocate_unattributed(
                self.usage, {("u3", "a1")}, dry_run=True, correct_users=True
            )
        self.assertIn(
            "WARNING:slurm_quota:CPU consumption of user u3 not corrected, no CPU "
            "consumption in Slurm accounting in the selected period",
            log_cm.output,
        )
        self.assertEqual(report.corrected("cpu"), [])

    def test_corrections_restricted_to_selected_users(self):
        with self.assertLogs("slurm_quota", level="WARNING") as log_cm:
            report = reallocate_unattributed(
                self.usage,
                set(),
                dry_run=True,
                correct_users=True,
                only_users={"u1", "unknown"},
            )
        self.assertNotIn(self.CORRECTED, log_cm.output)
        self.assertIn(
            "WARNING:slurm_quota:CPU consumption of user u1 not corrected, it is "
            "not among the users only paired with an account that gets more than "
            "its unattributed consumption",
            log_cm.output,
        )
        self.assertIn(
            "WARNING:slurm_quota:User unknown selected for correction has no "
            "unattributed consumption",
            log_cm.output,
        )
        self.assertEqual(report.corrected("cpu"), [])
        self.assertEqual(report.reallocated_cpu_seconds, 2000 * 60)

    def test_selected_users_are_corrected(self):
        with patch("slurm_quota.reallocate.logger"):
            report = reallocate_unattributed(
                self.usage,
                set(),
                dry_run=True,
                correct_users=True,
                only_users={"u3"},
            )
        self.assertEqual(report.corrected("cpu"), ["u3"])


class TestUserList(SlurmQuotaTestCase):
    def test_parse(self):
        self.assertEqual(_user_list("u1, u2,,u3"), {"u1", "u2", "u3"})

    def test_empty(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            _user_list(" , ")
