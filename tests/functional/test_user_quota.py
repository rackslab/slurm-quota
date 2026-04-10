"""Functional tests: `user-quota` subcommand."""

from __future__ import annotations

from unittest.mock import patch

from tests.functional.functional_base import FunctionalCLIBase


class TestUserQuotaCommand(FunctionalCLIBase):
    def test_user_quota_creates_user_when_missing(self):
        with patch.object(self.sq, "get_current_user", return_value="root"):
            with patch.object(self.sq, "set_database_permissions"):
                with self.capture_stdout() as out:
                    self.run_main(["slurm-quota", "user-quota", "userNew", "500"])
        self.assertEqual(
            out.getvalue(),
            "Successfully set quota for user userNew: 500 CPU minutes\n",
        )
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes FROM users WHERE username = ?",
                ("userNew",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 500)

    def test_user_quota_updates_existing_user(self):
        self.init_db()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, quota_cpu_minutes) VALUES (?, ?)",
                ("userUp", 10),
            )
            conn.commit()
        with patch.object(self.sq, "get_current_user", return_value="root"):
            with patch.object(self.sq, "set_database_permissions"):
                with self.capture_stdout() as out:
                    self.run_main(["slurm-quota", "user-quota", "userUp", "500"])
        self.assertEqual(
            out.getvalue(),
            "Successfully set quota for user userUp: 500 CPU minutes\n",
        )
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes FROM users WHERE username = ?",
                ("userUp",),
            ).fetchone()
        self.assertEqual(row[0], 500)

    def test_user_quota_rejects_non_root(self):
        with patch.object(self.sq, "get_current_user", return_value="nobody"):
            with self.assertLogs("slurm_quota", level="ERROR") as log_cm:
                with self.assertRaises(SystemExit) as cm:
                    self.run_main(["slurm-quota", "user-quota", "x", "1"])
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:Set-quota command can only be executed by root "
                "user, not by nobody",
            ],
        )
