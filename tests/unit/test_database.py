"""Unit tests for slurm_quota.database."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

from slurm_quota.database import (
    adjust_consumed_minutes,
    grant_api_manager,
    init_database,
    is_api_manager,
    list_api_managers,
    list_users_with_roles,
    load_gpu_factors,
    prune_resources,
    query_accounts_aggregate,
    query_users_aggregate,
    revoke_api_manager,
    set_account_gpu_quota,
    set_account_quota,
    set_user_gpu_quota,
    set_user_quota,
    update_user_and_account_resources,
)

from tests.test_support import SlurmQuotaTestCase


class TestLoadGpuFactors(SlurmQuotaTestCase):
    def test_load_gpu_factors(self):
        factors = load_gpu_factors()
        self.assertEqual(factors["__default__"], 1.0)
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO gpu_factors (gpu_type, factor) VALUES (?, ?)",
                ("default", 1.25),
            )
            conn.execute(
                "INSERT INTO gpu_factors (gpu_type, factor) VALUES (?, ?)",
                ("h100", 2.5),
            )
            conn.commit()
        factors = load_gpu_factors()
        self.assertEqual(factors["__default__"], 1.25)
        self.assertEqual(factors["h100"], 2.5)


class TestPruneResources(SlurmQuotaTestCase):
    def test_prune_resources_dry_run_counts_without_deleting(self):
        init_database()
        with self.db_connection() as conn:
            conn.executemany(
                """
                INSERT INTO users (
                    username, total_consumed_cpu_minutes, total_consumed_gpu_minutes
                ) VALUES (?, ?, ?)
                """,
                [
                    ("u_free", 0, 0),
                    ("u_orphan_prealloc", 0, 0),
                    ("u_active_prealloc", 0, 0),
                    ("u_busy", 10, 0),
                ],
            )
            conn.executemany(
                """
                INSERT INTO accounts (
                    account, total_consumed_cpu_minutes, total_consumed_gpu_minutes
                ) VALUES (?, ?, ?)
                """,
                [
                    ("a_free", 0, 0),
                    ("a_orphan_prealloc", 0, 0),
                    ("a_active_prealloc", 0, 0),
                    ("a_busy", 5, 0),
                ],
            )
            conn.executemany(
                """
                INSERT INTO jobs_preallocations (
                    job_uuid, username, account, preallocated_cpu_minutes
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    ("uuid-orphan", "u_orphan_prealloc", "a_orphan_prealloc", 5),
                    ("uuid-active", "u_active_prealloc", "a_active_prealloc", 5),
                ],
            )
            conn.commit()

        with patch(
            "slurm_quota.slurm.collect_active_job_uuids", return_value={"uuid-active"}
        ):
            with self.assertLogs("slurm_quota", level="INFO") as log_cm:
                counts = prune_resources(
                    {"preallocs", "users", "accounts"}, dry_run=True
                )

        self.assertEqual(counts, {"preallocs": 1, "users": 3, "accounts": 3})
        self.assertIn(
            "INFO:slurm_quota:Eligible user for pruning: u_free",
            log_cm.output,
        )
        self.assertIn(
            "INFO:slurm_quota:Eligible user for pruning: u_orphan_prealloc",
            log_cm.output,
        )
        self.assertIn(
            "INFO:slurm_quota:Eligible user for pruning: u_active_prealloc",
            log_cm.output,
        )
        self.assertIn(
            "INFO:slurm_quota:Eligible account for pruning: a_free",
            log_cm.output,
        )
        self.assertIn(
            "INFO:slurm_quota:Eligible account for pruning: a_orphan_prealloc",
            log_cm.output,
        )
        self.assertIn(
            "INFO:slurm_quota:Eligible account for pruning: a_active_prealloc",
            log_cm.output,
        )
        with self.db_connection() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM jobs_preallocations").fetchone()[0],
                2,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 4
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 4
            )

    def test_prune_resources_does_not_call_squeue_with_db_open(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes, total_consumed_gpu_minutes) VALUES (?, ?, ?)",
                ("u1", 0, 0),
            )
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_cpu_minutes, total_consumed_gpu_minutes) VALUES (?, ?, ?)",
                ("a1", 0, 0),
            )
            conn.execute(
                """
                INSERT INTO jobs_preallocations (
                    job_uuid, username, account, preallocated_cpu_minutes
                ) VALUES (?, ?, ?, ?)
                """,
                ("uuid-orphan", "u1", "a1", 5),
            )
            conn.commit()

        # Count how many prune_resources DB contexts are currently open.
        # prune_resources uses "with sqlite3.connect(...)"; we wrap connect() so
        # __enter__/__exit__ increment/decrement this counter per connection.
        open_connections = 0
        real_connect = sqlite3.connect

        def tracking_connect(database, *args, **kwargs):
            conn = real_connect(database, *args, **kwargs)

            class TrackingConnection:
                def __enter__(self):
                    nonlocal open_connections
                    open_connections += 1
                    conn.__enter__()
                    return conn

                def __exit__(self, exc_type, exc, tb):
                    nonlocal open_connections
                    open_connections -= 1
                    return conn.__exit__(exc_type, exc, tb)

                def __getattr__(self, name):
                    return getattr(conn, name)

            return TrackingConnection()

        def collect_without_db_lock():
            # Called in place of squeue; must run after the prealloc SELECT
            # connection is closed (open_connections back to 0).
            self.assertEqual(open_connections, 0)
            return set()

        with patch(
            "slurm_quota.database.sqlite3.connect", side_effect=tracking_connect
        ):
            with patch(
                "slurm_quota.slurm.collect_active_job_uuids",
                side_effect=collect_without_db_lock,
            ):
                counts = prune_resources({"preallocs"}, dry_run=True)

        self.assertEqual(counts, {"preallocs": 1, "users": 0, "accounts": 0})

    def test_prune_resources_users_only_fails_if_prealloc_references_exist(self):
        init_database()
        with self.db_connection() as conn:
            conn.executemany(
                """
                INSERT INTO users (
                    username, total_consumed_cpu_minutes, total_consumed_gpu_minutes
                ) VALUES (?, ?, ?)
                """,
                [
                    ("u_free", 0, 0),
                    ("u_orphan_prealloc", 0, 0),
                    ("u_busy", 1, 0),
                ],
            )
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_cpu_minutes, total_consumed_gpu_minutes) VALUES (?, ?, ?)",
                ("a1", 0, 0),
            )
            conn.execute(
                """
                INSERT INTO jobs_preallocations (
                    job_uuid, username, account, preallocated_cpu_minutes
                ) VALUES (?, ?, ?, ?)
                """,
                ("uuid-orphan", "u_orphan_prealloc", "a1", 5),
            )
            conn.commit()

        with self.assertRaises(sqlite3.IntegrityError) as cm:
            prune_resources({"users"}, dry_run=False)
        self.assertIn("Failed to prune users", str(cm.exception))
        with self.db_connection() as conn:
            users = {
                row[0]
                for row in conn.execute("SELECT username FROM users ORDER BY username")
            }
            self.assertEqual(users, {"u_free", "u_orphan_prealloc", "u_busy"})
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM jobs_preallocations").fetchone()[0],
                1,
            )

    def test_prune_resources_all_deletes_orphan_prealloc_then_users_accounts(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes, total_consumed_gpu_minutes) VALUES (?, ?, ?)",
                ("u_orphan_prealloc", 0, 0),
            )
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_cpu_minutes, total_consumed_gpu_minutes) VALUES (?, ?, ?)",
                ("a_orphan_prealloc", 0, 0),
            )
            conn.execute(
                """
                INSERT INTO jobs_preallocations (
                    job_uuid, username, account, preallocated_cpu_minutes
                ) VALUES (?, ?, ?, ?)
                """,
                ("uuid-orphan", "u_orphan_prealloc", "a_orphan_prealloc", 5),
            )
            conn.commit()

        with patch("slurm_quota.slurm.collect_active_job_uuids", return_value=set()):
            with self.assertLogs("slurm_quota", level="INFO") as log_cm:
                counts = prune_resources(
                    {"preallocs", "users", "accounts"}, dry_run=False
                )

        self.assertEqual(counts, {"preallocs": 1, "users": 1, "accounts": 1})
        self.assertIn(
            "INFO:slurm_quota:Eligible user for pruning: u_orphan_prealloc",
            log_cm.output,
        )
        self.assertIn(
            "INFO:slurm_quota:Eligible account for pruning: a_orphan_prealloc",
            log_cm.output,
        )
        with self.db_connection() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM jobs_preallocations").fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 0
            )

    def test_prune_resources_applies_user_and_account_filters(self):
        init_database()
        with self.db_connection() as conn:
            conn.executemany(
                """
                INSERT INTO users (
                    username, total_consumed_cpu_minutes, total_consumed_gpu_minutes
                ) VALUES (?, ?, ?)
                """,
                [("u1", 0, 0), ("u2", 0, 0), ("u_busy", 1, 0)],
            )
            conn.executemany(
                """
                INSERT INTO accounts (
                    account, total_consumed_cpu_minutes, total_consumed_gpu_minutes
                ) VALUES (?, ?, ?)
                """,
                [("a1", 0, 0), ("a2", 0, 0), ("a_busy", 0, 1)],
            )
            conn.commit()

        with self.assertLogs("slurm_quota", level="INFO") as log_cm:
            counts = prune_resources(
                {"users", "accounts"},
                dry_run=True,
                user_filter="u1",
                account_filter="a2",
            )

        self.assertEqual(counts, {"preallocs": 0, "users": 1, "accounts": 1})
        self.assertIn(
            "INFO:slurm_quota:Eligible user for pruning: u1",
            log_cm.output,
        )
        self.assertNotIn(
            "INFO:slurm_quota:Eligible user for pruning: u2",
            log_cm.output,
        )
        self.assertIn(
            "INFO:slurm_quota:Eligible account for pruning: a2",
            log_cm.output,
        )
        self.assertNotIn(
            "INFO:slurm_quota:Eligible account for pruning: a1",
            log_cm.output,
        )

    def test_prune_resources_filtered_non_eligible_user_and_account(self):
        init_database()
        with self.db_connection() as conn:
            conn.executemany(
                """
                INSERT INTO users (
                    username, total_consumed_cpu_minutes, total_consumed_gpu_minutes
                ) VALUES (?, ?, ?)
                """,
                [("u_busy", 3, 0), ("u_zero", 0, 0)],
            )
            conn.executemany(
                """
                INSERT INTO accounts (
                    account, total_consumed_cpu_minutes, total_consumed_gpu_minutes
                ) VALUES (?, ?, ?)
                """,
                [("a_busy", 0, 4), ("a_zero", 0, 0)],
            )
            conn.commit()

        counts = prune_resources(
            {"users", "accounts"},
            dry_run=False,
            user_filter="u_busy",
            account_filter="a_busy",
        )
        self.assertEqual(counts, {"preallocs": 0, "users": 0, "accounts": 0})

        with self.db_connection() as conn:
            users = {
                row[0]
                for row in conn.execute("SELECT username FROM users ORDER BY username")
            }
            accounts = {
                row[0]
                for row in conn.execute("SELECT account FROM accounts ORDER BY account")
            }
            self.assertEqual(users, {"u_busy", "u_zero"})
            self.assertEqual(accounts, {"a_busy", "a_zero"})


class TestUpdateUserAndAccountResources(SlurmQuotaTestCase):
    def test_new_user_and_account_get_default_quotas_from_settings(self):
        init_database()
        with self.db_connection() as conn:
            conn.executemany(
                "UPDATE settings SET value = ? WHERE key = ?",
                [
                    ("5000", "default_user_quota_cpu_minutes"),
                    ("600", "default_user_quota_gpu_minutes"),
                    ("80000", "default_account_quota_cpu_minutes"),
                    ("9000", "default_account_quota_gpu_minutes"),
                ],
            )
            conn.commit()
        update_user_and_account_resources("newu", "newa", 1, None, 1)
        with self.db_connection() as conn:
            u = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM users WHERE username=?",
                ("newu",),
            ).fetchone()
            a = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM accounts WHERE account=?",
                ("newa",),
            ).fetchone()
        self.assertEqual(u, (5000, 600))
        self.assertEqual(a, (80000, 9000))

    def test_update_user_and_account_resources(self):
        init_database()
        status = update_user_and_account_resources("u1", "a1", 10, None, 2)
        self.assertEqual(status, "none")
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO jobs_preallocations (job_uuid, username, account, preallocated_cpu_minutes, array_size) VALUES (?, ?, ?, ?, ?)",
                ("uuid1", "u1", "a1", 5, 1),
            )
            conn.commit()
        status = update_user_and_account_resources("u1", "a1", 10, "uuid1", 0)
        self.assertEqual(status, "removed")


class TestSetUserQuota(SlurmQuotaTestCase):
    def test_insert_uses_default_gpu_from_settings(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = ?",
                ("4242", "default_user_quota_gpu_minutes"),
            )
            conn.commit()
        set_user_quota("fresh", 100)
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM users WHERE username=?",
                ("fresh",),
            ).fetchone()
        self.assertEqual(row, (100, 4242))

    def test_update_preserves_existing_gpu(self):
        init_database()
        set_user_gpu_quota("u", 99)
        with self.db_connection() as conn:
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = ?",
                ("111", "default_user_quota_cpu_minutes"),
            )
            conn.commit()
        set_user_quota("u", 500)
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM users WHERE username=?",
                ("u",),
            ).fetchone()
        self.assertEqual(row, (500, 99))


class TestSetAccountQuota(SlurmQuotaTestCase):
    def test_insert_uses_default_gpu_from_settings(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = ?",
                ("3333", "default_account_quota_gpu_minutes"),
            )
            conn.commit()
        set_account_quota("fresha", 50)
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM accounts WHERE account=?",
                ("fresha",),
            ).fetchone()
        self.assertEqual(row, (50, 3333))

    def test_update_preserves_existing_gpu(self):
        init_database()
        set_account_gpu_quota("a", 88)
        with self.db_connection() as conn:
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = ?",
                ("999", "default_account_quota_cpu_minutes"),
            )
            conn.commit()
        set_account_quota("a", 300)
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM accounts WHERE account=?",
                ("a",),
            ).fetchone()
        self.assertEqual(row, (300, 88))


class TestSetUserGpuQuota(SlurmQuotaTestCase):
    def test_insert_uses_default_cpu_from_settings(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = ?",
                ("5151", "default_user_quota_cpu_minutes"),
            )
            conn.commit()
        set_user_gpu_quota("freshg", 200)
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM users WHERE username=?",
                ("freshg",),
            ).fetchone()
        self.assertEqual(row, (5151, 200))

    def test_update_preserves_existing_cpu(self):
        init_database()
        set_user_quota("v", 400)
        with self.db_connection() as conn:
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = ?",
                ("222", "default_user_quota_gpu_minutes"),
            )
            conn.commit()
        set_user_gpu_quota("v", 77)
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM users WHERE username=?",
                ("v",),
            ).fetchone()
        self.assertEqual(row, (400, 77))


class TestSetAccountGpuQuota(SlurmQuotaTestCase):
    def test_insert_uses_default_cpu_from_settings(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = ?",
                ("6666", "default_account_quota_cpu_minutes"),
            )
            conn.commit()
        set_account_gpu_quota("freshag", 80)
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM accounts WHERE account=?",
                ("freshag",),
            ).fetchone()
        self.assertEqual(row, (6666, 80))

    def test_update_preserves_existing_cpu(self):
        init_database()
        set_account_quota("b", 250)
        with self.db_connection() as conn:
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = ?",
                ("888", "default_account_quota_gpu_minutes"),
            )
            conn.commit()
        set_account_gpu_quota("b", 44)
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM accounts WHERE account=?",
                ("b",),
            ).fetchone()
        self.assertEqual(row, (250, 44))


class TestAdjustConsumedMinutes(SlurmQuotaTestCase):
    def test_adjust_user_cpu_adds_minutes(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("alice", 100),
            )
            conn.commit()

        new_value = adjust_consumed_minutes("user", "alice", "cpu", 25)
        self.assertEqual(new_value, 125)

        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT total_consumed_cpu_minutes FROM users WHERE username = ?",
                ("alice",),
            ).fetchone()
        self.assertEqual(row, (125,))

    def test_adjust_account_gpu_clamps_to_zero(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_gpu_minutes) VALUES (?, ?)",
                ("hpc", 40),
            )
            conn.commit()

        new_value = adjust_consumed_minutes("account", "hpc", "gpu", -100)
        self.assertEqual(new_value, 0)

        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT total_consumed_gpu_minutes FROM accounts WHERE account = ?",
                ("hpc",),
            ).fetchone()
        self.assertEqual(row, (0,))

    def test_adjust_raises_for_missing_target(self):
        init_database()
        with self.assertRaisesRegex(ValueError, "User not found: missing"):
            adjust_consumed_minutes("user", "missing", "cpu", 1)

    def test_adjust_rejects_invalid_target_type(self):
        with self.assertRaisesRegex(ValueError, "Invalid target type: team"):
            adjust_consumed_minutes("team", "dev", "cpu", 1)

    def test_adjust_rejects_invalid_resource_type(self):
        with self.assertRaisesRegex(ValueError, "Invalid resource type: mem"):
            adjust_consumed_minutes("user", "alice", "mem", 1)


class TestQueryUsersAggregate(SlurmQuotaTestCase):
    def test_query_users_aggregate(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("u1", 10),
            )
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("a1", 20),
            )
            conn.execute(
                "INSERT INTO jobs_preallocations (job_uuid, username, account, preallocated_cpu_minutes, array_size) VALUES (?, ?, ?, ?, ?)",
                ("j1", "u1", "a1", 5, 2),
            )
            conn.commit()
            users = query_users_aggregate(conn)
        self.assertEqual(users[0]["total_preallocated_cpu_minutes"], 10)
        self.assertEqual(users[0]["job_count"], 2)


class TestQueryAccountsAggregate(SlurmQuotaTestCase):
    def test_query_accounts_aggregate(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("u1", 10),
            )
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("a1", 20),
            )
            conn.execute(
                "INSERT INTO jobs_preallocations (job_uuid, username, account, preallocated_cpu_minutes, array_size) VALUES (?, ?, ?, ?, ?)",
                ("j1", "u1", "a1", 5, 2),
            )
            conn.commit()
            accounts = query_accounts_aggregate(conn, {"a1"})
        self.assertEqual(accounts[0]["account"], "a1")


class TestApiManagers(SlurmQuotaTestCase):
    def setUp(self):
        super().setUp()
        init_database()

    def test_is_api_manager_false_until_granted(self):
        with self.db_connection() as conn:
            self.assertFalse(is_api_manager(conn, "carol"))
            grant_api_manager(conn, "carol")
            self.assertTrue(is_api_manager(conn, "carol"))

    def test_list_api_managers_returns_sorted_usernames(self):
        with self.db_connection() as conn:
            self.assertEqual(list_api_managers(conn), [])
            grant_api_manager(conn, "carol")
            grant_api_manager(conn, "bob")
            self.assertEqual(list_api_managers(conn), ["bob", "carol"])

    def test_grant_api_manager_is_idempotent(self):
        with self.db_connection() as conn:
            grant_api_manager(conn, "bob")
            grant_api_manager(conn, "bob")
            self.assertEqual(list_api_managers(conn), ["bob"])

    def test_revoke_api_manager_removes_entry(self):
        with self.db_connection() as conn:
            grant_api_manager(conn, "bob")
            revoke_api_manager(conn, "bob")
            self.assertFalse(is_api_manager(conn, "bob"))
            self.assertEqual(list_api_managers(conn), [])

    def test_revoke_api_manager_is_noop_when_missing(self):
        with self.db_connection() as conn:
            revoke_api_manager(conn, "missing")
            self.assertEqual(list_api_managers(conn), [])

    def test_list_users_with_roles_merges_users_admins_and_managers(self):
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("bob", 10),
            )
            grant_api_manager(conn, "carol")
            conn.commit()

            roles = {
                entry["username"]: entry["role"]
                for entry in list_users_with_roles(conn, {"alice"})
            }
        self.assertEqual(roles["alice"], "admin")
        self.assertEqual(roles["bob"], "user")
        self.assertEqual(roles["carol"], "manager")

    def test_list_users_with_roles_includes_admin_without_db_user_row(self):
        with self.db_connection() as conn:
            roles = list_users_with_roles(conn, {"alice"})
        self.assertEqual(roles, [{"username": "alice", "role": "admin"}])
