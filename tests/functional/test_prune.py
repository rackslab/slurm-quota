"""Functional tests: `prune` subcommand."""

from __future__ import annotations

from unittest.mock import patch

from tests.functional.functional_base import FunctionalCLIBase


class TestPruneCommand(FunctionalCLIBase):
    def test_prune_as_root(self):
        with patch.object(self.sq, "get_current_user", return_value="root"):
            with patch.object(self.sq, "prune_orphan_preallocations", return_value=0):
                with self.capture_stdout() as out:
                    self.run_main(["slurm-quota", "prune"])
                self.assertIn("No orphan", out.getvalue())
            with patch.object(self.sq, "prune_orphan_preallocations", return_value=3):
                with self.capture_stdout() as out:
                    self.run_main(["slurm-quota", "prune"])
                self.assertIn("3", out.getvalue())
