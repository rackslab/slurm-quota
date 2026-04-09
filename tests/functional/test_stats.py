"""Functional tests: `stats` subcommand and global `--debug`."""

from __future__ import annotations

from unittest.mock import patch

from tests.functional.functional_base import FunctionalCLIBase, FakeJsonUrlopenResponse


class TestStatsCommand(FunctionalCLIBase):
    def test_stats_variants_and_debug(self):
        payload = self.stats_json_payload()
        with patch.object(
            self.sq, "urlopen", return_value=FakeJsonUrlopenResponse(payload)
        ):
            with patch.object(self.sq, "get_current_user", return_value="alice"):
                with self.capture_stdout() as out:
                    self.run_main(["slurm-quota", "stats"])
                self.assertIn("alice", out.getvalue())

            for argv in [
                ["slurm-quota", "stats", "alice"],
                ["slurm-quota", "stats", "--all"],
                ["slurm-quota", "stats", "alice", "--all"],
                ["slurm-quota", "--debug", "stats", "--all"],
            ]:
                with self.capture_stdout() as out:
                    self.run_main(argv)
                self.assertIn("alice", out.getvalue())
