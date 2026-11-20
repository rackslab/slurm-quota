"""Unit tests for slurm_quota.scopes."""

from __future__ import annotations

from slurm_quota.scopes import (
    SCOPE_ROLES,
    SCOPES,
    scopes_for_role,
)
from tests.test_support import SlurmQuotaTestCase


class TestScopes(SlurmQuotaTestCase):
    def test_all_scopes_have_roles(self):
        self.assertEqual(set(SCOPES), set(SCOPE_ROLES))

    def test_scopes_for_role(self):
        self.assertEqual(scopes_for_role("admin"), list(SCOPES))
        self.assertEqual(
            scopes_for_role("operator"),
            ["stats:read", "quotas:read", "quotas:write"],
        )
        self.assertEqual(scopes_for_role("manager"), ["stats:read"])
        self.assertEqual(scopes_for_role("user"), ["stats:read"])
