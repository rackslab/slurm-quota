"""Functional tests: invoking CLI with no subcommand (prints help, exit 1)."""

from __future__ import annotations

from tests.functional.functional_base import FunctionalCLIBase


class TestMain(FunctionalCLIBase):
    def test_no_subcommand_exits_with_help(self):
        self.run_main_exit(["slurm-quota"], 1)
