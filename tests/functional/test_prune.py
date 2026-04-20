"""Functional tests: `prune` subcommand."""

from __future__ import annotations

from itertools import combinations
from unittest.mock import patch

from tests.functional.functional_base import FunctionalCLIBase


class TestPruneCommand(FunctionalCLIBase):
    def test_prune_selector_options_are_mutually_exclusive(self):
        selectors = ["--preallocs", "--users", "--accounts", "--all"]
        for left, right in combinations(selectors, 2):
            with self.subTest(left=left, right=right):
                with self.assertRaises(SystemExit) as cm:
                    self.run_main(["slurm-quota", "prune", left, right])
                self.assertEqual(cm.exception.code, 2)

    def test_prune_rejects_non_root(self):
        with patch.object(self.sq, "get_current_user", return_value="slurm"):
            with self.assertLogs("slurm_quota", level="ERROR") as log_cm:
                with self.assertRaises(SystemExit) as cm:
                    self.run_main(["slurm-quota", "prune"])
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:Prune command can only be executed by root user, "
                "not by slurm",
            ],
        )

    def test_prune_as_root_defaults_to_all_targets(self):
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
                    ("u_with_orphan", 0, 0),
                    ("u_active", 10, 0),
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
                    ("a_with_orphan", 0, 0),
                    ("a_active", 10, 0),
                ],
            )
            conn.executemany(
                """
                INSERT INTO jobs_preallocations (
                    job_uuid, username, account, preallocated_cpu_minutes
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    ("uuid-orphan", "u_with_orphan", "a_with_orphan", 5),
                    ("uuid-active", "u_active", "a_active", 5),
                ],
            )
            conn.commit()

        with patch.object(self.sq, "get_current_user", return_value="root"):
            with patch.object(
                self.sq, "collect_active_job_uuids", return_value={"uuid-active"}
            ):
                with self.assertLogs("slurm_quota", level="INFO") as log_cm:
                    with self.capture_stdout() as out:
                        self.run_main(["slurm-quota", "prune"])
        self.assertEqual(
            out.getvalue(),
            "Removed 1 orphan preallocation(s), 2 user(s), 2 account(s)\n",
        )
        self.assertIn(
            "INFO:slurm_quota:Eligible user for pruning: u_free",
            log_cm.output,
        )
        self.assertIn(
            "INFO:slurm_quota:Eligible user for pruning: u_with_orphan",
            log_cm.output,
        )
        self.assertIn(
            "INFO:slurm_quota:Eligible account for pruning: a_free",
            log_cm.output,
        )
        self.assertIn(
            "INFO:slurm_quota:Eligible account for pruning: a_with_orphan",
            log_cm.output,
        )
        with self.db_connection() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM jobs_preallocations").fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 1
            )

    def test_prune_only_preallocs(self):
        self.init_db()
        with self.db_connection() as conn:
            conn.executemany(
                """
                INSERT INTO users (
                    username, total_consumed_cpu_minutes, total_consumed_gpu_minutes
                ) VALUES (?, ?, ?)
                """,
                [("u1", 0, 0), ("u2", 0, 0)],
            )
            conn.executemany(
                """
                INSERT INTO accounts (
                    account, total_consumed_cpu_minutes, total_consumed_gpu_minutes
                ) VALUES (?, ?, ?)
                """,
                [("a1", 0, 0), ("a2", 0, 0)],
            )
            conn.executemany(
                """
                INSERT INTO jobs_preallocations (
                    job_uuid, username, account, preallocated_cpu_minutes
                ) VALUES (?, ?, ?, ?)
                """,
                [("uuid-orphan", "u1", "a1", 5), ("uuid-active", "u2", "a2", 5)],
            )
            conn.commit()
        with patch.object(self.sq, "get_current_user", return_value="root"):
            with patch.object(
                self.sq, "collect_active_job_uuids", return_value={"uuid-active"}
            ):
                with self.capture_stdout() as out:
                    self.run_main(["slurm-quota", "prune", "--preallocs"])
        self.assertEqual(
            out.getvalue(),
            "Removed 1 orphan preallocation(s), 0 user(s), 0 account(s)\n",
        )
        with self.db_connection() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM jobs_preallocations").fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 2
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 2
            )

    def test_prune_only_users(self):
        self.init_db()
        with self.db_connection() as conn:
            conn.executemany(
                """
                INSERT INTO users (
                    username, total_consumed_cpu_minutes, total_consumed_gpu_minutes
                ) VALUES (?, ?, ?)
                """,
                [("u_free", 0, 0), ("u_busy", 5, 0)],
            )
            conn.execute(
                """
                INSERT INTO accounts (
                    account, total_consumed_cpu_minutes, total_consumed_gpu_minutes
                ) VALUES (?, ?, ?)
                """,
                ("a1", 0, 0),
            )
            conn.commit()
        with patch.object(self.sq, "get_current_user", return_value="root"):
            with self.capture_stdout() as out:
                self.run_main(["slurm-quota", "prune", "--users"])
        self.assertEqual(
            out.getvalue(),
            "Removed 0 orphan preallocation(s), 1 user(s), 0 account(s)\n",
        )
        with self.db_connection() as conn:
            users = {
                row[0]
                for row in conn.execute("SELECT username FROM users ORDER BY username")
            }
            self.assertEqual(users, {"u_busy"})
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 1
            )

    def test_prune_only_accounts(self):
        self.init_db()
        with self.db_connection() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    username, total_consumed_cpu_minutes, total_consumed_gpu_minutes
                ) VALUES (?, ?, ?)
                """,
                ("u1", 0, 0),
            )
            conn.executemany(
                """
                INSERT INTO accounts (
                    account, total_consumed_cpu_minutes, total_consumed_gpu_minutes
                ) VALUES (?, ?, ?)
                """,
                [("a_free", 0, 0), ("a_busy", 0, 7)],
            )
            conn.commit()
        with patch.object(self.sq, "get_current_user", return_value="root"):
            with self.capture_stdout() as out:
                self.run_main(["slurm-quota", "prune", "--accounts"])
        self.assertEqual(
            out.getvalue(),
            "Removed 0 orphan preallocation(s), 0 user(s), 1 account(s)\n",
        )
        with self.db_connection() as conn:
            accounts = {
                row[0]
                for row in conn.execute("SELECT account FROM accounts ORDER BY account")
            }
            self.assertEqual(accounts, {"a_busy"})
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1
            )

    def test_prune_users_with_linked_prealloc_fails(self):
        self.init_db()
        with self.db_connection() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    username, total_consumed_cpu_minutes, total_consumed_gpu_minutes
                ) VALUES (?, ?, ?)
                """,
                ("u_link", 0, 0),
            )
            conn.execute(
                """
                INSERT INTO accounts (
                    account, total_consumed_cpu_minutes, total_consumed_gpu_minutes
                ) VALUES (?, ?, ?)
                """,
                ("a1", 0, 0),
            )
            conn.execute(
                """
                INSERT INTO jobs_preallocations (
                    job_uuid, username, account, preallocated_cpu_minutes
                ) VALUES (?, ?, ?, ?)
                """,
                ("uuid-linked", "u_link", "a1", 5),
            )
            conn.commit()

        with patch.object(self.sq, "get_current_user", return_value="root"):
            with self.assertLogs("slurm_quota", level="ERROR") as log_cm:
                with self.assertRaises(SystemExit) as cm:
                    self.run_main(["slurm-quota", "prune", "--users"])
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:Prune command failed: Failed to prune users: one "
                "or more users are still referenced by jobs preallocations",
            ],
        )
        with self.db_connection() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 1
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM jobs_preallocations").fetchone()[0],
                1,
            )

    def test_prune_accounts_with_linked_prealloc_fails(self):
        self.init_db()
        with self.db_connection() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    username, total_consumed_cpu_minutes, total_consumed_gpu_minutes
                ) VALUES (?, ?, ?)
                """,
                ("u1", 0, 0),
            )
            conn.execute(
                """
                INSERT INTO accounts (
                    account, total_consumed_cpu_minutes, total_consumed_gpu_minutes
                ) VALUES (?, ?, ?)
                """,
                ("a_link", 0, 0),
            )
            conn.execute(
                """
                INSERT INTO jobs_preallocations (
                    job_uuid, username, account, preallocated_cpu_minutes
                ) VALUES (?, ?, ?, ?)
                """,
                ("uuid-linked", "u1", "a_link", 5),
            )
            conn.commit()

        with patch.object(self.sq, "get_current_user", return_value="root"):
            with self.assertLogs("slurm_quota", level="ERROR") as log_cm:
                with self.assertRaises(SystemExit) as cm:
                    self.run_main(["slurm-quota", "prune", "--accounts"])
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:Prune command failed: Failed to prune accounts: one "
                "or more accounts are still referenced by jobs preallocations",
            ],
        )
        with self.db_connection() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 1
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM jobs_preallocations").fetchone()[0],
                1,
            )

    def test_prune_users_dry_run(self):
        self.init_db()
        with self.db_connection() as conn:
            conn.executemany(
                """
                INSERT INTO users (
                    username, total_consumed_cpu_minutes, total_consumed_gpu_minutes
                ) VALUES (?, ?, ?)
                """,
                [
                    ("u1", 0, 0),
                    ("u2", 0, 0),
                    ("u3", 0, 0),
                    ("u4", 12, 0),
                ],
            )
            conn.execute(
                """
                INSERT INTO accounts (
                    account, total_consumed_cpu_minutes, total_consumed_gpu_minutes
                ) VALUES (?, ?, ?)
                """,
                ("a1", 0, 0),
            )
            conn.execute(
                """
                INSERT INTO jobs_preallocations (
                    job_uuid, username, account, preallocated_cpu_minutes
                ) VALUES (?, ?, ?, ?)
                """,
                ("uuid1", "u2", "a1", 5),
            )
            conn.commit()

        with patch.object(self.sq, "get_current_user", return_value="root"):
            with self.assertLogs("slurm_quota", level="INFO") as log_cm:
                with self.capture_stdout() as out:
                    self.run_main(["slurm-quota", "prune", "--users", "--dry-run"])
        self.assertEqual(
            out.getvalue(),
            (
                "Dry-run: would remove 0 orphan preallocation(s), 3 user(s), "
                "0 account(s)\n"
            ),
        )
        self.assertIn(
            "INFO:slurm_quota:Eligible user for pruning: u1",
            log_cm.output,
        )
        self.assertIn(
            "INFO:slurm_quota:Eligible user for pruning: u2",
            log_cm.output,
        )
        self.assertIn(
            "INFO:slurm_quota:Eligible user for pruning: u3",
            log_cm.output,
        )
        # Verify that nothing was deleted
        with self.db_connection() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 4
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 1
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM jobs_preallocations").fetchone()[0],
                1,
            )

    def test_prune_prints_nothing_to_prune(self):
        self.init_db()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("u1", 10),
            )
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("a1", 10),
            )
            conn.execute(
                """
                INSERT INTO jobs_preallocations (
                    job_uuid, username, account, preallocated_cpu_minutes
                ) VALUES (?, ?, ?, ?)
                """,
                ("uuid-active", "u1", "a1", 5),
            )
            conn.commit()

        with patch.object(self.sq, "get_current_user", return_value="root"):
            with patch.object(
                self.sq, "collect_active_job_uuids", return_value={"uuid-active"}
            ):
                with self.capture_stdout() as out:
                    self.run_main(["slurm-quota", "prune", "--preallocs"])
        self.assertEqual(out.getvalue(), "Nothing to prune\n")
