"""Functional tests: `adjust` subcommand."""

from __future__ import annotations

from unittest.mock import patch

from tests.functional.functional_base import FunctionalCLIBase


class TestAdjustCommand(FunctionalCLIBase):
    def test_adjust_rejects_non_root(self):
        with patch.object(self.sq, "get_current_user", return_value="slurm"):
            with self.assertLogs("slurm_quota", level="ERROR") as log_cm:
                with self.assertRaises(SystemExit) as cm:
                    self.run_main(
                        [
                            "slurm-quota",
                            "adjust",
                            "--user",
                            "alice",
                            "--cpu",
                            "--minutes",
                            "+10",
                        ]
                    )
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:adjust command can only be executed by root user, "
                "not by slurm",
            ],
        )

    def test_adjust_user_cpu_minutes(self):
        self.init_db()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("alice", 100),
            )
            conn.commit()
        with patch.object(self.sq, "get_current_user", return_value="root"):
            with patch.object(self.sq, "set_database_permissions"):
                with self.capture_stdout() as out:
                    self.run_main(
                        [
                            "slurm-quota",
                            "adjust",
                            "--user",
                            "alice",
                            "--cpu",
                            "--minutes",
                            "+30",
                        ]
                    )
        self.assertEqual(
            out.getvalue(),
            (
                "Successfully adjusted CPU consumed time for user alice: "
                "+30 minutes (new total: 130 minutes)\n"
            ),
        )
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT total_consumed_cpu_minutes FROM users WHERE username = ?",
                ("alice",),
            ).fetchone()
        self.assertEqual(row, (130,))

    def test_adjust_account_gpu_hours(self):
        self.init_db()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_gpu_minutes) VALUES (?, ?)",
                ("hpc", 90),
            )
            conn.commit()
        with patch.object(self.sq, "get_current_user", return_value="root"):
            with patch.object(self.sq, "set_database_permissions"):
                with self.capture_stdout() as out:
                    self.run_main(
                        [
                            "slurm-quota",
                            "adjust",
                            "--account",
                            "hpc",
                            "--gpu",
                            "--hours",
                            "+2",
                        ]
                    )
        self.assertEqual(
            out.getvalue(),
            (
                "Successfully adjusted GPU consumed time for account hpc: "
                "+2 hours (new total: 210 minutes)\n"
            ),
        )
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT total_consumed_gpu_minutes FROM accounts WHERE account = ?",
                ("hpc",),
            ).fetchone()
        self.assertEqual(row, (210,))

    def test_adjust_clamps_to_zero(self):
        self.init_db()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_gpu_minutes) VALUES (?, ?)",
                ("alice", 30),
            )
            conn.commit()
        with patch.object(self.sq, "get_current_user", return_value="root"):
            with patch.object(self.sq, "set_database_permissions"):
                with self.capture_stdout() as out:
                    self.run_main(
                        [
                            "slurm-quota",
                            "adjust",
                            "--user",
                            "alice",
                            "--gpu",
                            "--minutes",
                            "-100",
                        ]
                    )
        self.assertEqual(
            out.getvalue(),
            (
                "Successfully adjusted GPU consumed time for user alice: "
                "-100 minutes (new total: 0 minutes)\n"
            ),
        )
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT total_consumed_gpu_minutes FROM users WHERE username = ?",
                ("alice",),
            ).fetchone()
        self.assertEqual(row, (0,))

    def test_adjust_rejects_unsigned_delta(self):
        self.run_main_exit(
            [
                "slurm-quota",
                "adjust",
                "--user",
                "alice",
                "--cpu",
                "--minutes",
                "30",
            ],
            2,
        )

    def test_adjust_fails_when_target_missing(self):
        self.init_db()
        with patch.object(self.sq, "get_current_user", return_value="root"):
            with patch.object(self.sq, "set_database_permissions"):
                with self.assertLogs("slurm_quota", level="ERROR") as log_cm:
                    with self.assertRaises(SystemExit) as cm:
                        self.run_main(
                            [
                                "slurm-quota",
                                "adjust",
                                "--account",
                                "missing",
                                "--cpu",
                                "--minutes",
                                "+5",
                            ]
                        )
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            ["ERROR:slurm_quota:adjust command failed: Account not found: missing"],
        )
