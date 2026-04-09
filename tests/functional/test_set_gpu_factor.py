"""Functional tests: `set-gpu-factor` subcommand."""

from __future__ import annotations

from unittest.mock import patch

from tests.functional.functional_base import FunctionalCLIBase


class TestSetGpuFactorCommand(FunctionalCLIBase):
    def test_set_gpu_factor_valid_as_root(self):
        with patch.object(self.sq, "get_current_user", return_value="root"):
            with patch.object(self.sq, "set_database_permissions"):
                with self.capture_stdout() as out:
                    self.run_main(
                        ["slurm-quota", "set-gpu-factor", "h100", "0.25"]
                    )
                self.assertIn("h100", out.getvalue())

    def test_set_gpu_factor_non_positive_exits(self):
        with patch.object(self.sq, "get_current_user", return_value="root"):
            self.run_main_exit(["slurm-quota", "set-gpu-factor", "x", "0"], 1)
