"""Functional tests: `user-quota` subcommand."""

from __future__ import annotations

from unittest.mock import patch

from tests.functional.functional_base import FunctionalCLIBase


class TestUserQuotaCommand(FunctionalCLIBase):
    def test_user_quota_as_root(self):
        with patch.object(self.sq, "get_current_user", return_value="root"):
            with patch.object(self.sq, "set_database_permissions"):
                with self.capture_stdout() as out:
                    self.run_main(["slurm-quota", "user-quota", "bob", "500"])
                text = out.getvalue()
                self.assertIn("bob", text)
                self.assertIn("500", text)

    def test_user_quota_rejects_non_root(self):
        with patch.object(self.sq, "get_current_user", return_value="nobody"):
            self.run_main_exit(["slurm-quota", "user-quota", "x", "1"], 1)
