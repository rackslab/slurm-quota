"""Functional tests: `account-quota` subcommand."""

from __future__ import annotations

from unittest.mock import patch

from tests.functional.functional_base import FunctionalCLIBase


class TestAccountQuotaCommand(FunctionalCLIBase):
    def test_account_quota_as_root(self):
        with patch.object(self.sq, "get_current_user", return_value="root"):
            with patch.object(self.sq, "set_database_permissions"):
                with self.capture_stdout() as out:
                    self.run_main(["slurm-quota", "account-quota", "acctX", "-1"])
                text = out.getvalue()
                self.assertIn("acctX", text)
                self.assertIn("-1", text)
