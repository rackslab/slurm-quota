"""Functional tests: `account-quota` subcommand."""

from __future__ import annotations

from unittest.mock import patch

from tests.functional.functional_base import FunctionalCLIBase


class TestAccountQuotaCommand(FunctionalCLIBase):
    def test_account_quota_creates_account_when_missing(self):
        with patch.object(self.sq, "get_current_user", return_value="root"):
            with patch.object(self.sq, "set_database_permissions"):
                with self.capture_stdout() as out:
                    self.run_main(["slurm-quota", "account-quota", "acctNew", "-1"])
        self.assertEqual(
            out.getvalue(),
            "Successfully set quota for account acctNew: -1 CPU minutes\n",
        )
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes FROM accounts WHERE account = ?",
                ("acctNew",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], -1)

    def test_account_quota_updates_existing_account(self):
        self.init_db()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO accounts (account, quota_cpu_minutes) VALUES (?, ?)",
                ("acctUp", 100),
            )
            conn.commit()
        with patch.object(self.sq, "get_current_user", return_value="root"):
            with patch.object(self.sq, "set_database_permissions"):
                with self.capture_stdout() as out:
                    self.run_main(["slurm-quota", "account-quota", "acctUp", "-1"])
        self.assertEqual(
            out.getvalue(),
            "Successfully set quota for account acctUp: -1 CPU minutes\n",
        )
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes FROM accounts WHERE account = ?",
                ("acctUp",),
            ).fetchone()
        self.assertEqual(row[0], -1)

    def test_account_quota_rejects_non_root(self):
        with patch.object(self.sq, "get_current_user", return_value="slurm"):
            with self.assertLogs("slurm_quota", level="ERROR") as log_cm:
                with self.assertRaises(SystemExit) as cm:
                    self.run_main(["slurm-quota", "account-quota", "acctX", "-1"])
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:Account-quota command can only be executed by "
                "root user, not by slurm",
            ],
        )
