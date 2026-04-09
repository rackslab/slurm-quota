"""Functional tests: `account-gpu-quota` subcommand."""

from __future__ import annotations

from unittest.mock import patch

from tests.functional.functional_base import FunctionalCLIBase


class TestAccountGpuQuotaCommand(FunctionalCLIBase):
    def test_account_gpu_quota_as_root(self):
        with patch.object(self.sq, "get_current_user", return_value="root"):
            with patch.object(self.sq, "set_database_permissions"):
                with self.capture_stdout() as out:
                    self.run_main(["slurm-quota", "account-gpu-quota", "acctY", "200"])
                self.assertIn("acctY", out.getvalue())
