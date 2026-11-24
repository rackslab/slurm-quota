"""Functional tests: `slurm-quota-reallocate` command."""

from __future__ import annotations

import os
import subprocess
import time
from unittest.mock import patch

from slurm_quota.database import init_database
from slurm_quota.migrate import UNATTRIBUTED
from tests.functional.functional_base import FunctionalCLIBase
from tests.testing_utils import fake_popen

# Current time when --endtime is omitted
NOW = 1_700_000_000


def _sacct_time(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(epoch))


class TestReallocateCommand(FunctionalCLIBase):
    def setUp(self):
        super().setUp()
        now_patcher = patch("slurm_quota.reallocate.time.time", return_value=NOW + 0.5)
        now_patcher.start()
        self.addCleanup(now_patcher.stop)

    def _seed_unattributed(self):
        init_database()
        with self.db_connection() as conn:
            self.insert_consumption(conn, "alice", UNATTRIBUTED, 100)
            self.insert_consumption(conn, "bob", UNATTRIBUTED, 50)
            self.insert_consumption(conn, UNATTRIBUTED, "hpc", 120)
            self.insert_consumption(conn, UNATTRIBUTED, "dev", 30)
            conn.commit()

    def _sacct_popen(self, args, **_kwargs):
        self.assertEqual(args[0], "sacct")
        end = NOW - 10
        lines = [
            f"1|alice|hpc|1|{end - 600}|{end}|cpu=1",
            f"2|alice|dev|1|{end - 200}|{end}|cpu=1",
            f"3|bob|hpc|1|{end - 100}|{end}|cpu=1",
        ]
        return fake_popen(args, "\n".join(lines) + "\n")

    def _sacctmgr_run(self, args, **_kwargs):
        self.assertEqual(args[0], "sacctmgr")
        return subprocess.CompletedProcess(args, 0, "alice|hpc\n", "")

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

    def test_reallocate_other_database_as_non_root(self):
        self._seed_unattributed()
        before = self._pairs()
        with (
            patch.dict(os.environ, {"SLURM_QUOTA_DB": self.db_path}),
            patch("slurm_quota.DB_PATH", "/nonexistent/slurm-quota.db"),
            patch("slurm_quota.auth.get_current_user", return_value="alice"),
            patch(
                "slurm_quota.slurm.subprocess.Popen",
                side_effect=self._sacct_popen,
            ),
            patch(
                "slurm_quota.slurm.subprocess.run",
                side_effect=self._sacctmgr_run,
            ),
            self.assertLogs("slurm_quota", level="WARNING") as log_cm,
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
            log_cm.output,
            [
                f"WARNING:slurm_quota:Using database {self.db_path} set in "
                "SLURM_QUOTA_DB",
            ],
        )
        self.assertIn("Dry-run: would reallocate 150 CPU minutes", out.getvalue())
        self.assertEqual(self._pairs(), before)

    def test_reallocate_other_database_not_found(self):
        missing = os.path.join(os.path.dirname(self.db_path), "missing.db")
        with (
            patch.dict(os.environ, {"SLURM_QUOTA_DB": missing}),
            patch("slurm_quota.auth.get_current_user", return_value="alice"),
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            self.run_reallocate_main(
                ["slurm-quota-reallocate", "--starttime", "2023-01-01"]
            )
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            [f"ERROR:slurm_quota:Database file not found: {missing}"],
        )
        self.assertFalse(os.path.exists(missing))

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
            patch("slurm_quota.slurm.subprocess.Popen") as popen_mock,
            patch("slurm_quota.slurm.subprocess.run") as run_mock,
            self.capture_stdout() as out,
        ):
            self.run_reallocate_main(
                ["slurm-quota-reallocate", "--starttime", "2023-01-01"]
            )
        self.assertEqual(out.getvalue(), "Nothing to reallocate\n")
        popen_mock.assert_not_called()
        run_mock.assert_not_called()

    def test_reallocate_explicit_endtime(self):
        self._seed_unattributed()
        with (
            patch("slurm_quota.auth.get_current_user", return_value="root"),
            patch(
                "slurm_quota.slurm.subprocess.Popen",
                side_effect=self._sacct_popen,
            ) as popen_mock,
            patch(
                "slurm_quota.slurm.subprocess.run",
                side_effect=self._sacctmgr_run,
            ),
            self.capture_stdout(),
        ):
            self.run_reallocate_main(
                [
                    "slurm-quota-reallocate",
                    "--starttime",
                    "2023-11-01",
                    "--endtime",
                    "2023-11-10T12:00:00",
                    "--dry-run",
                ]
            )
        sacct_args = popen_mock.call_args_list[0][0][0]
        self.assertEqual(sacct_args[0], "sacct")
        self.assertEqual(
            sacct_args[sacct_args.index("--endtime") + 1], "2023-11-10T12:00:00"
        )

    def test_reallocate_dry_run(self):
        self._seed_unattributed()
        before = self._pairs()
        with (
            patch("slurm_quota.auth.get_current_user", return_value="root"),
            patch(
                "slurm_quota.slurm.subprocess.Popen",
                side_effect=self._sacct_popen,
            ),
            patch(
                "slurm_quota.slurm.subprocess.run",
                side_effect=self._sacctmgr_run,
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
                "sacct-other-resource 0, associations 0\n"
                "GPU users and accounts paired from: sacct 0, "
                "sacct-other-resource 0, associations 0\n"
            ),
        )
        self.assertEqual(self._pairs(), before)

    def _run_correct_users(self, option):
        self._seed_unattributed()
        with self.db_connection() as conn:
            # bob has 500 minutes of unattributed consumption, only on hpc which
            # has 120 minutes, while bob consumed 100 seconds in Slurm accounting.
            self.insert_consumption(conn, "bob", UNATTRIBUTED, 450)
            conn.commit()
        with (
            patch("slurm_quota.auth.get_current_user", return_value="root"),
            patch(
                "slurm_quota.slurm.subprocess.Popen",
                side_effect=self._sacct_popen,
            ),
            patch(
                "slurm_quota.slurm.subprocess.run",
                side_effect=self._sacctmgr_run,
            ),
            self.assertLogs("slurm_quota", level="WARNING") as log_cm,
            self.capture_stdout() as out,
        ):
            self.run_reallocate_main(
                [
                    "slurm-quota-reallocate",
                    "--starttime",
                    "2023-11-01",
                    option,
                    "--dry-run",
                ]
            )
        return log_cm.output, out.getvalue()

    def _check_correct_users(self, option):
        logs, out = self._run_correct_users(option)
        self.assertIn(
            "WARNING:slurm_quota:CPU consumption of user bob corrected to 2 "
            "minutes instead of 500 minutes of unattributed consumption (-498), "
            "its consumption in Slurm accounting in the selected period, as users "
            "only paired with account hpc have more unattributed consumption than "
            "this account",
            logs,
        )
        self.assertIn(
            "CPU users corrected from Slurm accounting: 1 (see warnings)\n", out
        )

    def test_reallocate_correct_users(self):
        self._check_correct_users("--correct-users")

    def test_reallocate_correct_selected_users(self):
        self._check_correct_users("--correct-users=bob")

    def test_reallocate_suggests_correct_users(self):
        self._seed_unattributed()
        with self.db_connection() as conn:
            self.insert_consumption(conn, "bob", UNATTRIBUTED, 450)
            conn.commit()
        with (
            patch("slurm_quota.auth.get_current_user", return_value="root"),
            patch(
                "slurm_quota.slurm.subprocess.Popen",
                side_effect=self._sacct_popen,
            ),
            patch(
                "slurm_quota.slurm.subprocess.run",
                side_effect=self._sacctmgr_run,
            ),
            self.assertLogs("slurm_quota", level="WARNING"),
            self.capture_stdout() as out,
        ):
            self.run_reallocate_main(
                ["slurm-quota-reallocate", "--starttime", "2023-11-01", "--dry-run"]
            )
        self.assertIn(
            "CPU users that --correct-users would correct: 1 (see warnings)\n",
            out.getvalue(),
        )

    def test_reallocate(self):
        self._seed_unattributed()
        with (
            patch("slurm_quota.auth.get_current_user", return_value="root"),
            patch(
                "slurm_quota.slurm.subprocess.Popen",
                side_effect=self._sacct_popen,
            ) as popen_mock,
            patch(
                "slurm_quota.slurm.subprocess.run",
                side_effect=self._sacctmgr_run,
            ),
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
        # sacct queried up to now by default
        sacct_args = popen_mock.call_args_list[0][0][0]
        self.assertEqual(sacct_args[0], "sacct")
        self.assertEqual(
            sacct_args[sacct_args.index("--endtime") + 1], _sacct_time(NOW)
        )

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
                "slurm_quota.slurm.subprocess.Popen",
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
