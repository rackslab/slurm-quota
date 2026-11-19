"""Functional tests: `slurm-quota-sync` command."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from tests.functional.functional_base import FunctionalCLIBase


class TestSyncCommand(FunctionalCLIBase):
    def _associations(self) -> set[tuple[str, str]]:
        with self.db_connection() as conn:
            return set(conn.execute("SELECT username, account FROM slurm_associations"))

    def test_sync_rejects_non_slurm_user(self):
        with (
            patch("slurm_quota.auth.get_current_user", return_value="root"),
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            self.run_sync_main(["slurm-quota-sync"])
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:Sync command can only be executed by slurm user, "
                "not by root",
            ],
        )

    def test_sync_caches_slurm_associations(self):
        sacctmgr = subprocess.CompletedProcess(
            ["sacctmgr"], 0, "|root\nalice|hpc\nbob|hpc\nalice|dev\n", ""
        )
        with (
            patch("slurm_quota.auth.get_current_user", return_value="slurm"),
            patch("slurm_quota.slurm.subprocess.run", return_value=sacctmgr),
            self.assertLogs("slurm_quota", level="INFO") as log_cm,
        ):
            self.run_sync_main(["slurm-quota-sync"])
        self.assertIn(
            "INFO:slurm_quota:Synchronized 3 Slurm associations (3 added, 0 removed)",
            log_cm.output,
        )
        for username, account in (("alice", "dev"), ("alice", "hpc"), ("bob", "hpc")):
            self.assertIn(
                f"INFO:slurm_quota:Added Slurm association {username}/{account}",
                log_cm.output,
            )
        self.assertEqual(
            self._associations(), {("alice", "hpc"), ("bob", "hpc"), ("alice", "dev")}
        )

    def test_sync_logs_added_and_removed_associations(self):
        with (
            patch("slurm_quota.auth.get_current_user", return_value="slurm"),
            patch(
                "slurm_quota.slurm.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    ["sacctmgr"], 0, "alice|hpc\nbob|hpc\n", ""
                ),
            ),
        ):
            self.run_sync_main(["slurm-quota-sync"])

        with (
            patch("slurm_quota.auth.get_current_user", return_value="slurm"),
            patch(
                "slurm_quota.slurm.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    ["sacctmgr"], 0, "alice|hpc\nalice|dev\ncarol|dev\n", ""
                ),
            ),
            self.assertLogs("slurm_quota", level="INFO") as log_cm,
        ):
            self.run_sync_main(["slurm-quota-sync"])
        self.assertEqual(
            log_cm.output,
            [
                "INFO:slurm_quota:Added Slurm association alice/dev",
                "INFO:slurm_quota:Added Slurm association carol/dev",
                "INFO:slurm_quota:Removed Slurm association bob/hpc",
                "INFO:slurm_quota:Synchronized 3 Slurm associations "
                "(2 added, 1 removed)",
            ],
        )

    def test_sync_without_associations_keeps_cache(self):
        with (
            patch("slurm_quota.auth.get_current_user", return_value="slurm"),
            patch(
                "slurm_quota.slurm.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    ["sacctmgr"], 0, "alice|hpc\n", ""
                ),
            ),
        ):
            self.run_sync_main(["slurm-quota-sync"])

        # Only account level associations, without user
        with (
            patch("slurm_quota.auth.get_current_user", return_value="slurm"),
            patch(
                "slurm_quota.slurm.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    ["sacctmgr"], 0, "|root\n", ""
                ),
            ),
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            self.run_sync_main(["slurm-quota-sync"])
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:Sync command failed: no user association "
                "reported by sacctmgr, keeping cached associations",
            ],
        )
        self.assertEqual(self._associations(), {("alice", "hpc")})

    def test_sync_failure_keeps_cache(self):
        sacctmgr = subprocess.CompletedProcess(["sacctmgr"], 0, "alice|hpc\n", "")
        with (
            patch("slurm_quota.auth.get_current_user", return_value="slurm"),
            patch("slurm_quota.slurm.subprocess.run", return_value=sacctmgr),
        ):
            self.run_sync_main(["slurm-quota-sync"])

        with (
            patch("slurm_quota.auth.get_current_user", return_value="slurm"),
            patch(
                "slurm_quota.slurm.subprocess.run",
                side_effect=subprocess.CalledProcessError(1, "sacctmgr"),
            ),
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            self.run_sync_main(["slurm-quota-sync"])
        self.assertEqual(cm.exception.code, 1)
        self.assertTrue(
            log_cm.output[0].startswith(
                "ERROR:slurm_quota:Sync command failed: "
                "Failed to query associations with sacctmgr"
            )
        )
        self.assertEqual(self._associations(), {("alice", "hpc")})
