"""Functional tests: `gpu-factors` subcommand."""

from __future__ import annotations

from slurm_quota.database import init_database

from tests.functional.functional_base import FunctionalCLIBase


class TestGpuFactorsCommand(FunctionalCLIBase):
    def test_gpu_factors_without_database(self):
        with self.capture_stdout() as out:
            self.run_cli_main(["slurm-quota", "gpu-factors"])
        self.assertIn("Database not found", out.getvalue())

    def test_gpu_factors_with_rows(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO gpu_factors (gpu_type, factor) VALUES (?, ?)",
                ("a100", 1.5),
            )
            conn.commit()
        with self.capture_stdout() as out:
            self.run_cli_main(["slurm-quota", "gpu-factors"])
        self.assertIn("a100", out.getvalue())
