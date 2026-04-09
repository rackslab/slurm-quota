"""Functional tests: `user-gpu-quota` subcommand."""

from __future__ import annotations

from unittest.mock import patch

from tests.functional.functional_base import FunctionalCLIBase


class TestUserGpuQuotaCommand(FunctionalCLIBase):
    def test_user_gpu_quota_as_root(self):
        with patch.object(self.sq, "get_current_user", return_value="root"):
            with patch.object(self.sq, "set_database_permissions"):
                with self.capture_stdout() as out:
                    self.run_main(["slurm-quota", "user-gpu-quota", "bob", "100"])
                text = out.getvalue()
                self.assertIn("bob", text)
                self.assertTrue("GPU" in text or "gpu" in text.lower())
