"""Unit tests for slurm_quota.slurm."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from slurm_quota.slurm import (
    SlurmQueryError,
    calculate_consumed_gpu_seconds,
    collect_active_job_uuids,
    collect_pair_usage_from_sacct,
    get_account_users,
    get_associations,
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


class TestCalculateConsumedGpuSeconds(SlurmQuotaTestCase):
    def test_calculate_consumed_gpu_seconds(self):
        self.assertEqual(
            calculate_consumed_gpu_seconds({"h100": 2}, 30, {"__default__": 1.0}),
            60,
        )
        self.assertEqual(
            calculate_consumed_gpu_seconds(
                {"h100": 4}, 10, {"__default__": 1.0, "h100": 0.5}
            ),
            20,
        )

    def test_calculate_consumed_gpu_seconds_truncates_below_one_second(self):
        self.assertEqual(
            calculate_consumed_gpu_seconds({"h100": 1}, 3, {"__default__": 0.3}),
            0,
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


class TestGetAccountUsers(SlurmQuotaTestCase):
    @patch("slurm_quota.slurm.subprocess.run")
    def test_get_account_users(self, run_mock):
        run_mock.return_value = subprocess.CompletedProcess(
            ["sacctmgr"], 0, "alice\nbob\n", ""
        )
        self.assertEqual(get_account_users("hpc"), {"alice", "bob"})
        run_mock.assert_called_once()
        args = run_mock.call_args[0][0]
        self.assertEqual(args[0], "sacctmgr")
        self.assertIn("account=hpc", args)

    @patch("slurm_quota.slurm.subprocess.run")
    def test_get_account_users_empty_stdout(self, run_mock):
        run_mock.return_value = subprocess.CompletedProcess(["sacctmgr"], 0, "", "")
        self.assertEqual(get_account_users("hpc"), set())

    @patch("slurm_quota.slurm.subprocess.run")
    def test_get_account_users_sacctmgr_failure_returns_empty_set(self, run_mock):
        run_mock.side_effect = subprocess.CalledProcessError(1, "sacctmgr")
        self.assertEqual(get_account_users("hpc"), set())

    @patch("slurm_quota.slurm.subprocess.run")
    def test_get_account_users_missing_sacctmgr_returns_empty_set(self, run_mock):
        run_mock.side_effect = FileNotFoundError("sacctmgr")
        self.assertEqual(get_account_users("hpc"), set())


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
        self.assertEqual(out[2], 3600)
        self.assertEqual(out[5], 7200)

    @patch("slurm_quota.slurm.get_job_info_from_sacct", return_value=("uuid", ""))
    def test_get_job_info_from_environment_charges_sub_minute_job(self, _sacct):
        self.env(
            {
                "JOBID": "100",
                "USERNAME": "alice",
                "ACCOUNT": "proj",
                "PROCS": "4",
                "START": "1000",
                "END": "1030",
            }
        )
        out = get_job_info_from_environment()
        self.assertEqual(out[2], 120)
        self.assertEqual(out[5], 0)

    @patch("slurm_quota.slurm.get_job_info_from_sacct", return_value=("uuid", ""))
    def test_get_job_info_from_environment_without_end_charges_nothing(self, _sacct):
        self.env(
            {
                "JOBID": "101",
                "USERNAME": "alice",
                "ACCOUNT": "proj",
                "PROCS": "4",
                "START": "1000",
                "END": "0",
            }
        )
        out = get_job_info_from_environment()
        self.assertEqual(out[2], 0)


class TestCollectPairUsageFromSacct(SlurmQuotaTestCase):
    DAY = 86400

    @patch("slurm_quota.slurm.subprocess.run")
    def test_collects_usage_per_pair_in_chunks(self, run_mock):
        start = 1_700_000_000
        outputs = [
            # Chunk 1: [start, start + 1 day)
            "\n".join(
                [
                    f"1|alice|hpc|4|{start}|{start + 100}|cpu=4",
                    f"2|alice|hpc|2|{start + 10}|{start + 40}|cpu=2,gres/gpu:a100=2",
                    # Ends in chunk 2, counted there only
                    f"3|bob|dev|1|{start + 50}|{start + self.DAY + 5}|cpu=1",
                    "4|bob|dev|1|Unknown|Unknown|",
                    f"5||dev|1|{start}|{start + 10}|",
                    f"6|carol|hpc|8|{start + 20}|{start + 20}|cpu=8",
                ]
            ),
            # Chunk 2: [start + 1 day, start + 1.5 day)
            "\n".join(
                [
                    f"3|bob|dev|1|{start + 50}|{start + self.DAY + 5}|cpu=1",
                    f"7|alice|dev|1|{start + self.DAY}|{start + self.DAY + 60}|",
                ]
            ),
        ]
        run_mock.side_effect = [
            subprocess.CompletedProcess(["sacct"], 0, output + "\n", "")
            for output in outputs
        ]
        usage = collect_pair_usage_from_sacct(
            start, start + self.DAY + self.DAY // 2, 1, {"__default__": 1.0}
        )
        self.assertEqual(
            usage,
            {
                ("alice", "hpc"): (400 + 60, 60),
                ("bob", "dev"): (self.DAY + 5 - 50, 0),
                ("alice", "dev"): (60, 0),
            },
        )
        self.assertEqual(run_mock.call_count, 2)
        args, kwargs = run_mock.call_args_list[0]
        self.assertEqual(kwargs["env"]["SLURM_TIME_FORMAT"], "%s")
        self.assertIn("--allusers", args[0])
        self.assertIn("--allocations", args[0])

    @patch("slurm_quota.slurm.subprocess.run")
    def test_applies_gpu_factors(self, run_mock):
        run_mock.return_value = subprocess.CompletedProcess(
            ["sacct"], 0, "1|alice|hpc|1|100|200|gres/gpu:h100=2\n", ""
        )
        usage = collect_pair_usage_from_sacct(
            0, 1000, 30, {"__default__": 1.0, "h100": 1.5}
        )
        self.assertEqual(usage, {("alice", "hpc"): (100, 300)})

    @patch(
        "slurm_quota.slurm.subprocess.run",
        side_effect=FileNotFoundError("sacct"),
    )
    def test_raises_on_sacct_failure(self, _run_mock):
        with self.assertRaisesRegex(SlurmQueryError, "Failed to query jobs"):
            collect_pair_usage_from_sacct(0, 1000, 30, {"__default__": 1.0})


class TestGetAssociations(SlurmQuotaTestCase):
    @patch("slurm_quota.slurm.subprocess.run")
    def test_get_associations(self, run_mock):
        run_mock.return_value = subprocess.CompletedProcess(
            ["sacctmgr"], 0, "|root\nalice|hpc\nbob|hpc\nalice|dev\n\n", ""
        )
        self.assertEqual(
            get_associations(), {("alice", "hpc"), ("bob", "hpc"), ("alice", "dev")}
        )

    @patch(
        "slurm_quota.slurm.subprocess.run",
        side_effect=FileNotFoundError("sacctmgr"),
    )
    def test_get_associations_failure_returns_empty_set(self, _run_mock):
        with self.assertLogs("slurm_quota", level="WARNING"):
            self.assertEqual(get_associations(), set())
