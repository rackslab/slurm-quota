from __future__ import annotations

from unittest.mock import patch

from tests.test_support import SlurmQuotaTestCase


class TestGetJobInfoFromEnvironment(SlurmQuotaTestCase):
    @patch("slurm_quota.get_job_info_from_sacct", return_value=("uuid", "gres/gpu:x=2"))
    @patch("slurm_quota.load_gpu_factors", return_value={"__default__": 1.0})
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
        out = self.sq.get_job_info_from_environment()
        self.assertEqual(out[0], "alice")
        self.assertEqual(out[1], "99")
        self.assertEqual(out[2], 60)
        self.assertEqual(out[5], 120)
