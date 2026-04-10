"""Functional tests: `charge` subcommand."""

from __future__ import annotations

import os
from unittest.mock import patch

from tests.functional.functional_base import FunctionalCLIBase

# Slurm jobcomp-style env: 1 proc × (1860 − 60)s / 60 = 30 CPU minutes (START/END non-zero).
_SLURM_CHARGE_JOB_ENV = {
    "JOBID": "9",
    "ARRAYJOBID": "0",
    "ARRAYTASKID": "0",
    "USERNAME": "u1",
    "ACCOUNT": "a1",
    "PROCS": "1",
    "START": "60",
    "END": "1860",
}


class TestChargeCommand(FunctionalCLIBase):
    def test_charge_updates_database_as_slurm(self):
        self.init_db()
        self.env(_SLURM_CHARGE_JOB_ENV)
        with self.assertLogs("slurm_quota", level="INFO") as log_cm:
            with patch.object(self.sq, "get_current_user", return_value="slurm"):
                # Real env drives CPU math; sacct is not available in tests — fix UUID/GPU expectations.
                with patch.object(
                    self.sq, "get_job_info_from_sacct", return_value=(None, None)
                ):
                    self.run_main(["slurm-quota", "charge"])
        self.assertEqual(
            log_cm.output,
            [
                "WARNING:slurm_quota:No job UUID found in admin_comment for job 9",
                "INFO:slurm_quota:charge: user=u1 account=a1 job_id=9 +30 CPUmins +0 GPUmins "
                "uuid=none prealloc_status=none",
            ],
        )
        with self.db_connection() as conn:
            u = conn.execute(
                "SELECT total_consumed_cpu_minutes FROM users WHERE username=?",
                ("u1",),
            ).fetchone()
            a = conn.execute(
                "SELECT total_consumed_cpu_minutes FROM accounts WHERE account=?",
                ("a1",),
            ).fetchone()
        self.assertEqual(u[0], 30)
        self.assertEqual(a[0], 30)

    def test_charge_initializes_database_when_missing(self):
        self.assertFalse(
            os.path.exists(self.db_path),
            "database file should not exist before charge",
        )
        self.env(_SLURM_CHARGE_JOB_ENV)
        with self.assertLogs("slurm_quota", level="INFO") as log_cm:
            with patch.object(self.sq, "get_current_user", return_value="slurm"):
                with patch.object(
                    self.sq, "get_job_info_from_sacct", return_value=(None, None)
                ):
                    self.run_main(["slurm-quota", "charge"])
        self.assertTrue(os.path.exists(self.db_path))
        self.assertEqual(
            log_cm.output,
            [
                "INFO:slurm_quota:Database initialized successfully",
                f"INFO:slurm_quota:Database permissions set for {self.db_path}",
                "WARNING:slurm_quota:No job UUID found in admin_comment for job 9",
                "INFO:slurm_quota:charge: user=u1 account=a1 job_id=9 +30 CPUmins +0 GPUmins "
                "uuid=none prealloc_status=none",
            ],
        )
        with self.db_connection() as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            }
        self.assertGreaterEqual(
            tables,
            {"users", "accounts", "jobs_preallocations", "gpu_factors"},
        )

    def test_charge_removes_preallocation_when_array_size_one(self):
        self.init_db()
        job_uuid = "prealloc-job-uuid"
        with self.db_connection() as conn:
            conn.execute(
                """
                INSERT INTO jobs_preallocations
                    (job_uuid, username, account, preallocated_cpu_minutes, array_size)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_uuid, "u1", "a1", 100, 1),
            )
            conn.commit()
        self.env(_SLURM_CHARGE_JOB_ENV)
        with self.assertLogs("slurm_quota", level="INFO") as log_cm:
            with patch.object(self.sq, "get_current_user", return_value="slurm"):
                with patch.object(
                    self.sq,
                    "get_job_info_from_sacct",
                    return_value=(job_uuid, None),
                ):
                    self.run_main(["slurm-quota", "charge"])
        self.assertEqual(
            log_cm.output,
            [
                "INFO:slurm_quota:charge: user=u1 account=a1 job_id=9 +30 CPUmins +0 GPUmins "
                f"uuid={job_uuid} prealloc_status=removed",
            ],
        )
        with self.db_connection() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM jobs_preallocations WHERE job_uuid = ?",
                (job_uuid,),
            ).fetchone()[0]
            u = conn.execute(
                "SELECT total_consumed_cpu_minutes FROM users WHERE username=?",
                ("u1",),
            ).fetchone()
            a = conn.execute(
                "SELECT total_consumed_cpu_minutes FROM accounts WHERE account=?",
                ("a1",),
            ).fetchone()
        self.assertEqual(n, 0)
        self.assertEqual(u[0], 30)
        self.assertEqual(a[0], 30)

    def test_charge_rejects_non_slurm_user(self):
        with patch.object(self.sq, "get_current_user", return_value="root"):
            with self.assertLogs("slurm_quota", level="ERROR") as log_cm:
                with self.assertRaises(SystemExit) as cm:
                    self.run_main(["slurm-quota", "charge"])
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:Charge command can only be executed by slurm user, "
                "not by root",
            ],
        )
