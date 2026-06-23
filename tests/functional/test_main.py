"""Functional tests: invoking CLI with no subcommand (prints help, exit 1)."""

from __future__ import annotations

from slurm_quota import APP_VERSION

from tests.functional.functional_base import FunctionalCLIBase


class TestMain(FunctionalCLIBase):
    def test_no_subcommand_exits_with_help(self):
        self.run_cli_main_exit(["slurm-quota"], 1)

    def test_version_option_prints_version_and_exits_zero(self):
        with self.capture_stdout() as stdout:
            self.run_cli_main_exit(["slurm-quota", "--version"], 0)
        self.assertEqual(stdout.getvalue().strip(), f"slurm-quota {APP_VERSION}")

    def test_global_debug_and_quiet_are_mutually_exclusive(self):
        self.run_cli_main_exit(["slurm-quota", "--debug", "--quiet", "stats"], 2)
