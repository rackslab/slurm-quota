"""Functional tests: invoking CLI with no subcommand (prints help, exit 1)."""

from __future__ import annotations

from tests.functional.functional_base import FunctionalCLIBase


class TestMain(FunctionalCLIBase):
    def test_no_subcommand_exits_with_help(self):
        self.run_main_exit(["slurm-quota"], 1)

    def test_version_option_prints_version_and_exits_zero(self):
        with self.capture_stdout() as stdout:
            self.run_main_exit(["slurm-quota", "--version"], 0)
        self.assertEqual(
            stdout.getvalue().strip(), f"slurm-quota {self.sq.APP_VERSION}"
        )
