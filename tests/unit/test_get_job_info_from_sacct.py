from __future__ import annotations

import subprocess
from unittest.mock import patch

from tests.test_support import SlurmQuotaTestCase


class TestGetJobInfoFromSacct(SlurmQuotaTestCase):
    @patch("slurm_quota.subprocess.run")
    def test_get_job_info_from_sacct(self, run_mock):
        run_mock.return_value = subprocess.CompletedProcess(
            ["sacct"], 0, "uuid-1|gres/gpu:a100=1\n", ""
        )
        uuid, tres = self.sq.get_job_info_from_sacct("123")
        self.assertEqual(uuid, "uuid-1")
        self.assertEqual(tres, "gres/gpu:a100=1")
