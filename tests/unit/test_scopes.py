"""Unit tests for slurm_quota.scopes."""

from __future__ import annotations

from slurm_quota.scopes import SCOPE_ROLES, SCOPES, scopes_for_role
from tests.test_support import SlurmQuotaTestCase


class TestScopes(SlurmQuotaTestCase):
    def test_all_scopes_have_roles(self):
        self.assertEqual(set(SCOPES), set(SCOPE_ROLES))

    def test_scopes_for_role(self):
        self.assertEqual(
            scopes_for_role("admin"),
            [scope for scope in SCOPES if scope != "stats:read:managed"],
        )
        self.assertEqual(
            scopes_for_role("operator"),
            ["stats:read:own", "stats:read:all", "quotas:read", "quotas:write"],
        )
        self.assertEqual(
            scopes_for_role("manager"), ["stats:read:own", "stats:read:managed"]
        )
        self.assertEqual(scopes_for_role("user"), ["stats:read:own"])
