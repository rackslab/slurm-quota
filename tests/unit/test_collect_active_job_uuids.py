from __future__ import annotations

import subprocess
from unittest.mock import patch

from tests.test_support import SlurmQuotaTestCase


class TestCollectActiveJobUuids(SlurmQuotaTestCase):
    @patch("slurm_quota.subprocess.run")
    def test_collect_active_job_uuids(self, run_mock):
        run_mock.return_value = subprocess.CompletedProcess(
            ["squeue"], 0, "123   my-uuid\n", ""
        )
        self.assertEqual(self.sq.collect_active_job_uuids(), {"my-uuid"})
