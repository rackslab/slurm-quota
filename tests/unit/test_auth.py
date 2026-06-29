"""Unit tests for slurm_quota.auth."""

from __future__ import annotations

from unittest.mock import patch

from slurm_quota import auth
from tests.test_support import SlurmQuotaTestCase


class TestRequireSlurmUser(SlurmQuotaTestCase):
    def test_accepts_slurm_user(self):
        with patch("slurm_quota.auth.get_current_user", return_value="slurm"):
            auth.require_slurm_user()

    def test_rejects_non_slurm_user(self):
        with (
            patch("slurm_quota.auth.get_current_user", return_value="root"),
            self.assertRaises(PermissionError) as cm,
        ):
            auth.require_slurm_user()
        self.assertEqual(str(cm.exception), "Must be run as slurm user, not root")
