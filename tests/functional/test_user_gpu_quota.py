"""Functional tests: `user-gpu-quota` subcommand."""

from __future__ import annotations

from unittest.mock import patch

from tests.functional.functional_base import FunctionalCLIBase


class TestUserGpuQuotaCommand(FunctionalCLIBase):
    def test_user_gpu_quota_creates_user_when_missing(self):
        with patch.object(self.sq, "get_current_user", return_value="root"):
            with patch.object(self.sq, "set_database_permissions"):
                with self.capture_stdout() as out:
                    self.run_main(
                        ["slurm-quota", "user-gpu-quota", "userGpuNew", "100"]
                    )
        self.assertEqual(
            out.getvalue(),
            "Successfully set GPU quota for user userGpuNew: 100 GPU minutes\n",
        )
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_gpu_minutes FROM users WHERE username = ?",
                ("userGpuNew",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 100)

    def test_user_gpu_quota_updates_existing_user(self):
        self.init_db()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, quota_gpu_minutes) VALUES (?, ?)",
                ("userGpuUp", 20),
            )
            conn.commit()
        with patch.object(self.sq, "get_current_user", return_value="root"):
            with patch.object(self.sq, "set_database_permissions"):
                with self.capture_stdout() as out:
                    self.run_main(["slurm-quota", "user-gpu-quota", "userGpuUp", "100"])
        self.assertEqual(
            out.getvalue(),
            "Successfully set GPU quota for user userGpuUp: 100 GPU minutes\n",
        )
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_gpu_minutes FROM users WHERE username = ?",
                ("userGpuUp",),
            ).fetchone()
        self.assertEqual(row[0], 100)

    def test_user_gpu_quota_rejects_non_root(self):
        with patch.object(self.sq, "get_current_user", return_value="slurm"):
            with self.assertLogs("slurm_quota", level="ERROR") as log_cm:
                with self.assertRaises(SystemExit) as cm:
                    self.run_main(["slurm-quota", "user-gpu-quota", "bob", "100"])
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:User-gpu-quota command can only be executed by "
                "root user, not by slurm",
            ],
        )
