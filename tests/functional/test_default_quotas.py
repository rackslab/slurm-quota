"""Functional tests: `default-quotas` subcommand."""

from __future__ import annotations

from slurm_quota.database import init_database

import os
from textwrap import dedent

from tests.functional.functional_base import FunctionalCLIBase


class TestDefaultQuotasCommand(FunctionalCLIBase):
    def test_default_quotas_initializes_db_and_prints_defaults(self):
        with self.capture_stdout() as out:
            self.run_cli_main(["slurm-quota", "default-quotas"])
        self.assertEqual(
            out.getvalue(),
            dedent(
                """
                Default Quotas For New Entities
                ----------------------------------------
                user_cpu_minutes         ∞
                user_gpu_minutes         ∞
                account_cpu_minutes      ∞
                account_gpu_minutes      ∞
                """
            ).lstrip("\n"),
        )
        self.assertTrue(os.path.isfile(self.db_path))

    def test_default_quotas_reflects_settings_table(self):
        init_database()
        self.update_settings(
            default_user_quota_cpu_minutes=100,
            default_user_quota_gpu_minutes=200,
            default_account_quota_cpu_minutes=300,
            default_account_quota_gpu_minutes=400,
        )
        with self.capture_stdout() as out:
            self.run_cli_main(["slurm-quota", "default-quotas"])
        self.assertEqual(
            out.getvalue(),
            dedent(
                """
                Default Quotas For New Entities
                ----------------------------------------
                user_cpu_minutes         100
                user_gpu_minutes         200
                account_cpu_minutes      300
                account_gpu_minutes      400
                """
            ).lstrip("\n"),
        )

    def test_default_quotas_shows_infinity_only_for_negative_one(self):
        init_database()
        self.update_settings(
            default_user_quota_cpu_minutes=-1,
            default_user_quota_gpu_minutes=0,
            default_account_quota_cpu_minutes=-1,
            default_account_quota_gpu_minutes=42,
        )
        with self.capture_stdout() as out:
            self.run_cli_main(["slurm-quota", "default-quotas"])
        self.assertEqual(
            out.getvalue(),
            dedent(
                """
                Default Quotas For New Entities
                ----------------------------------------
                user_cpu_minutes         ∞
                user_gpu_minutes         0
                account_cpu_minutes      ∞
                account_gpu_minutes      42
                """
            ).lstrip("\n"),
        )
