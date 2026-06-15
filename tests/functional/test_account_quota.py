"""Functional tests: `account-quota` subcommand."""

from __future__ import annotations

from slurm_quota.database import init_database

from unittest.mock import patch

from tests.functional.functional_base import FunctionalCLIBase


class TestAccountQuotaCommand(FunctionalCLIBase):
    def test_account_quota_creates_account_when_missing(self):
        with patch("slurm_quota.auth.get_current_user", return_value="root"):
            with patch("slurm_quota.database.set_database_permissions"):
                with self.capture_stdout() as out:
                    self.run_main(
                        ["slurm-quota", "account-quota", "astrophysics", "-1"]
                    )
        self.assertEqual(
            out.getvalue(),
            "Successfully set quota for account astrophysics: -1 CPU minutes\n",
        )
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes FROM accounts WHERE account = ?",
                ("astrophysics",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], -1)

    def test_account_quota_applies_default_gpu_from_settings_on_create(self):
        init_database()
        self.update_settings(default_account_quota_gpu_minutes=3333)
        with patch("slurm_quota.auth.get_current_user", return_value="root"):
            with patch("slurm_quota.database.set_database_permissions"):
                self.run_main(
                    ["slurm-quota", "account-quota", "molecular_dynamics", "50"]
                )
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM accounts WHERE account = ?",
                ("molecular_dynamics",),
            ).fetchone()
        self.assertEqual(row, (50, 3333))

    def test_account_quota_updates_existing_account(self):
        init_database()
        self.update_settings(default_account_quota_gpu_minutes=88888)
        with self.db_connection() as conn:
            conn.execute(
                """
                INSERT INTO accounts (account, quota_cpu_minutes, quota_gpu_minutes)
                VALUES (?, ?, ?)
                """,
                ("oceanography", 100, 4444),
            )
            conn.commit()
        with patch("slurm_quota.auth.get_current_user", return_value="root"):
            with patch("slurm_quota.database.set_database_permissions"):
                with self.capture_stdout() as out:
                    self.run_main(
                        ["slurm-quota", "account-quota", "oceanography", "-1"]
                    )
        self.assertEqual(
            out.getvalue(),
            "Successfully set quota for account oceanography: -1 CPU minutes\n",
        )
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM accounts WHERE account = ?",
                ("oceanography",),
            ).fetchone()
        self.assertEqual(row, (-1, 4444))

    def test_account_quota_rejects_non_root(self):
        with patch("slurm_quota.auth.get_current_user", return_value="slurm"):
            with self.assertLogs("slurm_quota", level="ERROR") as log_cm:
                with self.assertRaises(SystemExit) as cm:
                    self.run_main(["slurm-quota", "account-quota", "reserved_io", "-1"])
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:Account-quota command can only be executed by "
                "root user, not by slurm",
            ],
        )
