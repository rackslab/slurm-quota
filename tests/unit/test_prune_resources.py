from __future__ import annotations

import sqlite3
from unittest.mock import patch

from tests.test_support import SlurmQuotaTestCase


class TestPruneResources(SlurmQuotaTestCase):
    def test_prune_resources_dry_run_counts_without_deleting(self):
        self.init_db()
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

        with patch.object(
            self.sq, "collect_active_job_uuids", return_value={"uuid-active"}
        ):
            with self.assertLogs("slurm_quota", level="INFO") as log_cm:
                counts = self.sq.prune_resources(
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
        self.init_db()
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

        with patch.object(self.sq.sqlite3, "connect", side_effect=tracking_connect):
            with patch.object(
                self.sq,
                "collect_active_job_uuids",
                side_effect=collect_without_db_lock,
            ):
                counts = self.sq.prune_resources({"preallocs"}, dry_run=True)

        self.assertEqual(counts, {"preallocs": 1, "users": 0, "accounts": 0})

    def test_prune_resources_users_only_fails_if_prealloc_references_exist(self):
        self.init_db()
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
            self.sq.prune_resources({"users"}, dry_run=False)
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
        self.init_db()
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

        with patch.object(self.sq, "collect_active_job_uuids", return_value=set()):
            with self.assertLogs("slurm_quota", level="INFO") as log_cm:
                counts = self.sq.prune_resources(
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
        self.init_db()
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
            counts = self.sq.prune_resources(
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
        self.init_db()
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

        counts = self.sq.prune_resources(
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
