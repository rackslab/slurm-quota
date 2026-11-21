"""Unit tests for slurm_quota.serve.jwt."""

from __future__ import annotations

import tempfile
from pathlib import Path

from rfl.authentication.errors import JWTDecodeError
from rfl.authentication.jwt import JWTBaseManager
from rfl.authentication.user import AuthenticatedUser

from slurm_quota.serve.jwt import SlurmQuotaJWTManager
from tests.test_support import SlurmQuotaTestCase


class TestSlurmQuotaJWTManager(SlurmQuotaTestCase):
    def setUp(self):
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.key = Path(self._tmpdir.name) / "jwt.key"
        self.jwt = SlurmQuotaJWTManager.key(
            audience="slurm-quota", algorithm="HS256", path=self.key, create=True
        )
        self.user = AuthenticatedUser(login="alice", groups=["users"])

    def _raw_token(self, claims: dict) -> str:
        base = {"aud": "slurm-quota", "sub": "alice", "groups": [], **claims}
        return JWTBaseManager.generate(self.jwt, 1, base)

    def test_user_token_without_scopes(self):
        decoded = self.jwt.decode(self.jwt.generate(self.user, 7))
        self.assertEqual(decoded.login, "alice")
        self.assertEqual(decoded.groups, ["users"])
        self.assertIsNone(decoded.scopes)

    def test_user_token_with_scopes(self):
        decoded = self.jwt.decode(self._raw_token({"scopes": ["stats:read:own"]}))
        self.assertEqual(decoded.scopes, {"stats:read:own"})

    def test_rejects_invalid_scopes(self):
        token = self._raw_token({"scopes": "stats:read:own"})
        with self.assertRaisesRegex(JWTDecodeError, "Token scopes are invalid"):
            self.jwt.decode(token)

    def test_rejects_invalid_audience(self):
        other = SlurmQuotaJWTManager.key(
            audience="other", algorithm="HS256", path=self.key
        )
        with self.assertRaises(JWTDecodeError):
            self.jwt.decode(other.generate(self.user, 7))
