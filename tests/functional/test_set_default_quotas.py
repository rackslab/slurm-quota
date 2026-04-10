"""Functional tests: `set-default-quotas` subcommand."""

from __future__ import annotations

from unittest.mock import patch

from tests.functional.functional_base import FunctionalCLIBase


class TestSetDefaultQuotasCommand(FunctionalCLIBase):
    def test_set_default_quotas_updates_all_keys(self):
        self.init_db()
        with patch.object(self.sq, "get_current_user", return_value="root"):
            with patch.object(self.sq, "set_database_permissions"):
                with self.capture_stdout() as out:
                    self.run_main(
                        [
                            "slurm-quota",
                            "set-default-quotas",
                            "--user-cpu",
                            "10",
                            "--user-gpu",
                            "20",
                            "--account-cpu",
                            "30",
                            "--account-gpu",
                            "40",
                        ]
                    )
        self.assertEqual(
            out.getvalue(),
            "Successfully updated default quotas\n",
        )
        with self.db_connection() as conn:
            rows = dict(conn.execute("SELECT key, value FROM settings").fetchall())
        self.assertEqual(rows["default_user_quota_cpu_minutes"], "10")
        self.assertEqual(rows["default_user_quota_gpu_minutes"], "20")
        self.assertEqual(rows["default_account_quota_cpu_minutes"], "30")
        self.assertEqual(rows["default_account_quota_gpu_minutes"], "40")

    def test_set_default_quotas_partial_only_changes_given_keys(self):
        self.init_db()
        self.update_settings(
            default_user_quota_cpu_minutes=1,
            default_user_quota_gpu_minutes=2,
            default_account_quota_cpu_minutes=3,
            default_account_quota_gpu_minutes=4,
        )
        with patch.object(self.sq, "get_current_user", return_value="root"):
            with patch.object(self.sq, "set_database_permissions"):
                with self.capture_stdout() as out:
                    self.run_main(
                        ["slurm-quota", "set-default-quotas", "--user-cpu", "999"]
                    )
        self.assertEqual(
            out.getvalue(),
            "Successfully updated default quotas\n",
        )
        with self.db_connection() as conn:
            rows = dict(conn.execute("SELECT key, value FROM settings").fetchall())
        self.assertEqual(rows["default_user_quota_cpu_minutes"], "999")
        self.assertEqual(rows["default_user_quota_gpu_minutes"], "2")
        self.assertEqual(rows["default_account_quota_cpu_minutes"], "3")
        self.assertEqual(rows["default_account_quota_gpu_minutes"], "4")

    def test_set_default_quotas_rejects_non_root(self):
        self.init_db()
        with patch.object(self.sq, "get_current_user", return_value="slurm"):
            with self.assertLogs("slurm_quota", level="ERROR") as log_cm:
                with self.assertRaises(SystemExit) as cm:
                    self.run_main(
                        [
                            "slurm-quota",
                            "set-default-quotas",
                            "--user-cpu",
                            "1",
                        ]
                    )
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:set-default-quotas command can only be executed "
                "by root user, not by slurm",
            ],
        )

    def test_set_default_quotas_requires_at_least_one_flag(self):
        with patch.object(self.sq, "get_current_user", return_value="root"):
            with patch.object(self.sq, "set_database_permissions"):
                with self.assertLogs("slurm_quota", level="ERROR") as log_cm:
                    with self.assertRaises(SystemExit) as cm:
                        self.run_main(["slurm-quota", "set-default-quotas"])
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:At least one option must be provided: --user-cpu, "
                "--user-gpu, --account-cpu, --account-gpu",
            ],
        )
