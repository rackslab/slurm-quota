"""Functional tests: `account-gpu-quota` subcommand."""

from __future__ import annotations

from slurm_quota.database import init_database

from unittest.mock import patch

from tests.functional.functional_base import FunctionalCLIBase


class TestAccountGpuQuotaCommand(FunctionalCLIBase):
    def test_account_gpu_quota_creates_account_when_missing(self):
        with patch("slurm_quota.auth.get_current_user", return_value="root"):
            with patch("slurm_quota.database.set_database_permissions"):
                with self.capture_stdout() as out:
                    self.run_main(
                        ["slurm-quota", "account-gpu-quota", "genomics_facility", "200"]
                    )
        self.assertEqual(
            out.getvalue(),
            "Successfully set GPU quota for account genomics_facility: 200 GPU minutes\n",
        )
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_gpu_minutes FROM accounts WHERE account = ?",
                ("genomics_facility",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 200)

    def test_account_gpu_quota_applies_default_cpu_from_settings_on_create(self):
        init_database()
        self.update_settings(default_account_quota_cpu_minutes=6666)
        with patch("slurm_quota.auth.get_current_user", return_value="root"):
            with patch("slurm_quota.database.set_database_permissions"):
                self.run_main(
                    ["slurm-quota", "account-gpu-quota", "neuro_render", "80"]
                )
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM accounts WHERE account = ?",
                ("neuro_render",),
            ).fetchone()
        self.assertEqual(row, (6666, 80))

    def test_account_gpu_quota_updates_existing_account(self):
        init_database()
        self.update_settings(default_account_quota_cpu_minutes=55555)
        with self.db_connection() as conn:
            conn.execute(
                """
                INSERT INTO accounts (account, quota_cpu_minutes, quota_gpu_minutes)
                VALUES (?, ?, ?)
                """,
                ("weather_cluster", 2222, 50),
            )
            conn.commit()
        with patch("slurm_quota.auth.get_current_user", return_value="root"):
            with patch("slurm_quota.database.set_database_permissions"):
                with self.capture_stdout() as out:
                    self.run_main(
                        ["slurm-quota", "account-gpu-quota", "weather_cluster", "200"]
                    )
        self.assertEqual(
            out.getvalue(),
            "Successfully set GPU quota for account weather_cluster: 200 GPU minutes\n",
        )
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM accounts WHERE account = ?",
                ("weather_cluster",),
            ).fetchone()
        self.assertEqual(row, (2222, 200))

    def test_account_gpu_quota_rejects_non_root(self):
        with patch("slurm_quota.auth.get_current_user", return_value="slurm"):
            with self.assertLogs("slurm_quota", level="ERROR") as log_cm:
                with self.assertRaises(SystemExit) as cm:
                    self.run_main(
                        ["slurm-quota", "account-gpu-quota", "legacy_archive", "200"]
                    )
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:Account-gpu-quota command can only be executed by "
                "root user, not by slurm",
            ],
        )
