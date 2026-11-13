"""Functional tests: `slurm-quota-reallocate` command."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from slurm_quota.database import init_database
from slurm_quota.migrate import CONSUMPTION_MIGRATED_AT, UNATTRIBUTED
from tests.functional.functional_base import FunctionalCLIBase

MIGRATED_AT = 1_700_000_000


class TestReallocateCommand(FunctionalCLIBase):
    def _seed_unattributed(self, migrated_at=True):
        init_database()
        with self.db_connection() as conn:
            self.insert_consumption(conn, "alice", UNATTRIBUTED, 100)
            self.insert_consumption(conn, "bob", UNATTRIBUTED, 50)
            self.insert_consumption(conn, UNATTRIBUTED, "hpc", 120)
            self.insert_consumption(conn, UNATTRIBUTED, "dev", 30)
            if migrated_at:
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?)",
                    (CONSUMPTION_MIGRATED_AT, str(MIGRATED_AT)),
                )
            conn.commit()

    def _slurm_side_effect(self, args, **_kwargs):
        if args[0] == "sacct":
            end = MIGRATED_AT - 10
            lines = [
                f"1|alice|hpc|1|{end - 600}|{end}|cpu=1",
                f"2|alice|dev|1|{end - 200}|{end}|cpu=1",
                f"3|bob|hpc|1|{end - 100}|{end}|cpu=1",
            ]
            return subprocess.CompletedProcess(args, 0, "\n".join(lines) + "\n", "")
        if args[0] == "sacctmgr":
            return subprocess.CompletedProcess(args, 0, "alice|hpc\n", "")
        raise AssertionError(f"unexpected command {args}")

    def _pairs(self):
        with self.db_connection() as conn:
            return conn.execute(
                "SELECT username, account, consumed_cpu_seconds "
                "FROM user_account_consumption ORDER BY username, account"
            ).fetchall()

    def test_reallocate_rejects_non_root(self):
        with (
            patch("slurm_quota.auth.get_current_user", return_value="slurm"),
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            self.run_reallocate_main(
                ["slurm-quota-reallocate", "--starttime", "2023-01-01"]
            )
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:Reallocate command can only be executed by root "
                "user, not by slurm",
            ],
        )

    def test_reallocate_requires_starttime(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_reallocate_main(["slurm-quota-reallocate"])
        self.assertEqual(cm.exception.code, 2)

    def test_reallocate_rejects_invalid_date(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_reallocate_main(
                ["slurm-quota-reallocate", "--starttime", "yesterday"]
            )
        self.assertEqual(cm.exception.code, 2)

    def test_reallocate_nothing_to_do(self):
        init_database()
        with (
            patch("slurm_quota.auth.get_current_user", return_value="root"),
            patch("slurm_quota.slurm.subprocess.run") as run_mock,
            self.capture_stdout() as out,
        ):
            self.run_reallocate_main(
                ["slurm-quota-reallocate", "--starttime", "2023-01-01"]
            )
        self.assertEqual(out.getvalue(), "Nothing to reallocate\n")
        run_mock.assert_not_called()

    def test_reallocate_requires_endtime_without_migration_time(self):
        self._seed_unattributed(migrated_at=False)
        with (
            patch("slurm_quota.auth.get_current_user", return_value="root"),
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            self.run_reallocate_main(
                ["slurm-quota-reallocate", "--starttime", "2023-01-01"]
            )
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:Migration time to user/account pairs not found, "
                "--endtime is required",
            ],
        )

    def test_reallocate_dry_run(self):
        self._seed_unattributed()
        before = self._pairs()
        with (
            patch("slurm_quota.auth.get_current_user", return_value="root"),
            patch(
                "slurm_quota.slurm.subprocess.run",
                side_effect=self._slurm_side_effect,
            ),
            self.capture_stdout() as out,
        ):
            self.run_reallocate_main(
                [
                    "slurm-quota-reallocate",
                    "--starttime",
                    "2023-11-01",
                    "--dry-run",
                ]
            )
        self.assertEqual(
            out.getvalue(),
            (
                "Dry-run: would reallocate 150 CPU minutes and 0 GPU minutes of "
                "unattributed consumption to 3 user/account pair(s)\n"
                "CPU users and accounts paired from: sacct 2, "
                "sacct-other-resource 0, associations 0, all 0\n"
                "GPU users and accounts paired from: sacct 0, "
                "sacct-other-resource 0, associations 0, all 0\n"
            ),
        )
        self.assertEqual(self._pairs(), before)

    def test_reallocate(self):
        self._seed_unattributed()
        with (
            patch("slurm_quota.auth.get_current_user", return_value="root"),
            patch(
                "slurm_quota.slurm.subprocess.run",
                side_effect=self._slurm_side_effect,
            ) as run_mock,
            self.capture_stdout() as out,
        ):
            self.run_reallocate_main(
                ["slurm-quota-reallocate", "--starttime", "2023-11-01"]
            )
        self.assertIn(
            "Reallocated 150 CPU minutes and 0 GPU minutes of unattributed "
            "consumption to 3 user/account pair(s)\n",
            out.getvalue(),
        )
        pairs = self._pairs()
        self.assertEqual(
            [pair[:2] for pair in pairs],
            [
                ("alice", "dev"),
                ("alice", "hpc"),
                ("bob", "hpc"),
            ],
        )
        # User and account totals are preserved
        per_user: dict[str, int] = {}
        per_account: dict[str, int] = {}
        for user, account, cpu in pairs:
            per_user[user] = per_user.get(user, 0) + cpu
            per_account[account] = per_account.get(account, 0) + cpu
        self.assertEqual(per_user, {"alice": 6000, "bob": 3000})
        self.assertEqual(per_account, {"hpc": 7200, "dev": 1800})
        with self.db_connection() as conn:
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM users WHERE username = ?", (UNATTRIBUTED,)
                ).fetchone()
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM accounts WHERE account = ?", (UNATTRIBUTED,)
                ).fetchone()
            )
        # sacct queried up to migration time
        sacct_args = run_mock.call_args_list[0][0][0]
        self.assertEqual(sacct_args[0], "sacct")

        # Second run has nothing to do
        with (
            patch("slurm_quota.auth.get_current_user", return_value="root"),
            self.capture_stdout() as out,
        ):
            self.run_reallocate_main(
                ["slurm-quota-reallocate", "--starttime", "2023-11-01"]
            )
        self.assertEqual(out.getvalue(), "Nothing to reallocate\n")

    def test_reallocate_reports_sacct_failure(self):
        self._seed_unattributed()
        with (
            patch("slurm_quota.auth.get_current_user", return_value="root"),
            patch(
                "slurm_quota.slurm.subprocess.run",
                side_effect=FileNotFoundError("sacct"),
            ),
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            self.run_reallocate_main(
                ["slurm-quota-reallocate", "--starttime", "2023-11-01"]
            )
        self.assertEqual(cm.exception.code, 1)
        self.assertIn(
            "ERROR:slurm_quota:Reallocation failed: Failed to query jobs with sacct",
            log_cm.output[0],
        )
