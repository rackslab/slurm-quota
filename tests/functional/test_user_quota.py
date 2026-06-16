"""Functional tests: `user-quota` subcommand."""

from __future__ import annotations

from slurm_quota.database import init_database

from unittest.mock import patch

from tests.functional.functional_base import FunctionalCLIBase


class TestUserQuotaCommand(FunctionalCLIBase):
    def test_user_quota_creates_user_when_missing(self):
        with patch("slurm_quota.auth.get_current_user", return_value="root"):
            with patch("slurm_quota.database.set_database_permissions"):
                with self.capture_stdout() as out:
                    self.run_cli_main(["slurm-quota", "user-quota", "elena", "500"])
        self.assertEqual(
            out.getvalue(),
            "Successfully set quota for user elena: 500 CPU minutes\n",
        )
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes FROM users WHERE username = ?",
                ("elena",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 500)

    def test_user_quota_applies_default_gpu_from_settings_on_create(self):
        init_database()
        self.update_settings(default_user_quota_gpu_minutes=4242)
        with patch("slurm_quota.auth.get_current_user", return_value="root"):
            with patch("slurm_quota.database.set_database_permissions"):
                self.run_cli_main(["slurm-quota", "user-quota", "marcus", "500"])
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM users WHERE username = ?",
                ("marcus",),
            ).fetchone()
        self.assertEqual(row, (500, 4242))

    def test_user_quota_updates_existing_user(self):
        init_database()
        self.update_settings(default_user_quota_gpu_minutes=99999)
        with self.db_connection() as conn:
            conn.execute(
                """
                INSERT INTO users (username, quota_cpu_minutes, quota_gpu_minutes)
                VALUES (?, ?, ?)
                """,
                ("sofia", 10, 8888),
            )
            conn.commit()
        with patch("slurm_quota.auth.get_current_user", return_value="root"):
            with patch("slurm_quota.database.set_database_permissions"):
                with self.capture_stdout() as out:
                    self.run_cli_main(["slurm-quota", "user-quota", "sofia", "500"])
        self.assertEqual(
            out.getvalue(),
            "Successfully set quota for user sofia: 500 CPU minutes\n",
        )
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM users WHERE username = ?",
                ("sofia",),
            ).fetchone()
        self.assertEqual(row, (500, 8888))

    def test_user_quota_rejects_non_root(self):
        with patch("slurm_quota.auth.get_current_user", return_value="nobody"):
            with self.assertLogs("slurm_quota", level="ERROR") as log_cm:
                with self.assertRaises(SystemExit) as cm:
                    self.run_cli_main(["slurm-quota", "user-quota", "taylor", "1"])
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:Set-quota command can only be executed by root "
                "user, not by nobody",
            ],
        )
