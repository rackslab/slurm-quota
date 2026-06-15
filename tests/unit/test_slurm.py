"""Unit tests for slurm_quota.slurm."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from slurm_quota.slurm import (
    calculate_consumed_gpu_minutes,
    collect_active_job_uuids,
    get_job_info_from_environment,
    get_job_info_from_sacct,
    get_user_accounts,
    parse_alloc_tres,
)

from tests.test_support import SlurmQuotaTestCase


class TestParseAllocTres(SlurmQuotaTestCase):
    def test_parse_alloc_tres(self):
        self.assertEqual(parse_alloc_tres(""), {})
        self.assertEqual(
            parse_alloc_tres("cpu=1,gres/gpu:h100=2,gres/gpu:h200=1"),
            {"h100": 2, "h200": 1},
        )
        self.assertEqual(
            parse_alloc_tres("gres/gpu:a100=1,gres/gpu:a100=2"),
            {"a100": 3},
        )


class TestCalculateConsumedGpuMinutes(SlurmQuotaTestCase):
    def test_calculate_consumed_gpu_minutes(self):
        self.assertEqual(
            calculate_consumed_gpu_minutes({"h100": 2}, 30, {"__default__": 1.0}),
            60,
        )
        self.assertEqual(
            calculate_consumed_gpu_minutes(
                {"h100": 4}, 10, {"__default__": 1.0, "h100": 0.5}
            ),
            20,
        )


class TestGetJobInfoFromSacct(SlurmQuotaTestCase):
    @patch("slurm_quota.slurm.subprocess.run")
    def test_get_job_info_from_sacct(self, run_mock):
        run_mock.return_value = subprocess.CompletedProcess(
            ["sacct"], 0, "uuid-1|gres/gpu:a100=1\n", ""
        )
        uuid, tres = get_job_info_from_sacct("123")
        self.assertEqual(uuid, "uuid-1")
        self.assertEqual(tres, "gres/gpu:a100=1")


class TestGetUserAccounts(SlurmQuotaTestCase):
    @patch("slurm_quota.slurm.subprocess.run")
    def test_get_user_accounts(self, run_mock):
        run_mock.return_value = subprocess.CompletedProcess(
            ["sacctmgr"], 0, "acc1\nacc2\n", ""
        )
        self.assertEqual(get_user_accounts("u"), {"acc1", "acc2"})


class TestCollectActiveJobUuids(SlurmQuotaTestCase):
    @patch("slurm_quota.slurm.subprocess.run")
    def test_collect_active_job_uuids(self, run_mock):
        run_mock.return_value = subprocess.CompletedProcess(
            ["squeue"], 0, "123   my-uuid\n", ""
        )
        self.assertEqual(collect_active_job_uuids(), {"my-uuid"})


class TestGetJobInfoFromEnvironment(SlurmQuotaTestCase):
    @patch(
        "slurm_quota.slurm.get_job_info_from_sacct",
        return_value=("uuid", "gres/gpu:x=2"),
    )
    @patch("slurm_quota.database.load_gpu_factors", return_value={"__default__": 1.0})
    def test_get_job_info_from_environment(self, _factors, _sacct):
        self.env(
            {
                "JOBID": "99",
                "USERNAME": "alice",
                "ACCOUNT": "proj",
                "PROCS": "1",
                "START": "60",
                "END": "3660",
            }
        )
        out = get_job_info_from_environment()
        self.assertEqual(out[0], "alice")
        self.assertEqual(out[1], "99")
        self.assertEqual(out[2], 60)
        self.assertEqual(out[5], 120)
