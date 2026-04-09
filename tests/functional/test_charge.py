"""Functional tests: `charge` subcommand."""

from __future__ import annotations

from unittest.mock import patch

from tests.functional.functional_base import FunctionalCLIBase


class TestChargeCommand(FunctionalCLIBase):
    def test_charge_updates_database_as_slurm(self):
        self.init_db()
        with patch.object(self.sq, "get_current_user", return_value="slurm"):
            with patch.object(
                self.sq,
                "get_job_info_from_environment",
                return_value=("u1", "9", 30, "a1", None, 0),
            ):
                self.run_main(["slurm-quota", "charge"])
        with self.db_connection() as conn:
            u = conn.execute(
                "SELECT total_consumed_cpu_minutes FROM users WHERE username=?",
                ("u1",),
            ).fetchone()
            a = conn.execute(
                "SELECT total_consumed_cpu_minutes FROM accounts WHERE account=?",
                ("a1",),
            ).fetchone()
        self.assertEqual(u[0], 30)
        self.assertEqual(a[0], 30)

    def test_charge_rejects_non_slurm_user(self):
        with patch.object(self.sq, "get_current_user", return_value="root"):
            self.run_main_exit(["slurm-quota", "charge"], 1)
