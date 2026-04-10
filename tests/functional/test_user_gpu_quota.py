"""Functional tests: `user-gpu-quota` subcommand."""

from __future__ import annotations

from unittest.mock import patch

from tests.functional.functional_base import FunctionalCLIBase


class TestUserGpuQuotaCommand(FunctionalCLIBase):
    def test_user_gpu_quota_creates_user_when_missing(self):
        with patch.object(self.sq, "get_current_user", return_value="root"):
            with patch.object(self.sq, "set_database_permissions"):
                with self.capture_stdout() as out:
                    self.run_main(["slurm-quota", "user-gpu-quota", "liam", "100"])
        self.assertEqual(
            out.getvalue(),
            "Successfully set GPU quota for user liam: 100 GPU minutes\n",
        )
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_gpu_minutes FROM users WHERE username = ?",
                ("liam",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 100)

    def test_user_gpu_quota_applies_default_cpu_from_settings_on_create(self):
        self.init_db()
        self.update_settings(default_user_quota_cpu_minutes=5151)
        with patch.object(self.sq, "get_current_user", return_value="root"):
            with patch.object(self.sq, "set_database_permissions"):
                self.run_main(["slurm-quota", "user-gpu-quota", "amara", "200"])
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM users WHERE username = ?",
                ("amara",),
            ).fetchone()
        self.assertEqual(row, (5151, 200))

    def test_user_gpu_quota_updates_existing_user(self):
        self.init_db()
        self.update_settings(default_user_quota_cpu_minutes=77777)
        with self.db_connection() as conn:
            conn.execute(
                """
                INSERT INTO users (username, quota_cpu_minutes, quota_gpu_minutes)
                VALUES (?, ?, ?)
                """,
                ("noah", 3333, 20),
            )
            conn.commit()
        with patch.object(self.sq, "get_current_user", return_value="root"):
            with patch.object(self.sq, "set_database_permissions"):
                with self.capture_stdout() as out:
                    self.run_main(["slurm-quota", "user-gpu-quota", "noah", "100"])
        self.assertEqual(
            out.getvalue(),
            "Successfully set GPU quota for user noah: 100 GPU minutes\n",
        )
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM users WHERE username = ?",
                ("noah",),
            ).fetchone()
        self.assertEqual(row, (3333, 100))

    def test_user_gpu_quota_rejects_non_root(self):
        with patch.object(self.sq, "get_current_user", return_value="slurm"):
            with self.assertLogs("slurm_quota", level="ERROR") as log_cm:
                with self.assertRaises(SystemExit) as cm:
                    self.run_main(["slurm-quota", "user-gpu-quota", "owen", "100"])
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:User-gpu-quota command can only be executed by "
                "root user, not by slurm",
            ],
        )
