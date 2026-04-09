from __future__ import annotations

import subprocess
from unittest.mock import patch

from tests.test_support import SlurmQuotaTestCase


class TestGetUserAccounts(SlurmQuotaTestCase):
    @patch("slurm_quota.subprocess.run")
    def test_get_user_accounts(self, run_mock):
        run_mock.return_value = subprocess.CompletedProcess(
            ["sacctmgr"], 0, "acc1\nacc2\n", ""
        )
        self.assertEqual(self.sq.get_user_accounts("u"), {"acc1", "acc2"})
