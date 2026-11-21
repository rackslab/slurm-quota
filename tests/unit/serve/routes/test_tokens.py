"""Unit tests for token scopes enforcement."""

from __future__ import annotations

from rfl.authentication.jwt import JWTManager
from rfl.authentication.user import AuthenticatedUser

from slurm_quota.database import grant_operator
from slurm_quota.serve.settings import load_serve_settings
from tests.test_support import serve_conf_defs
from tests.unit.serve.routes.base import ServeRoutesTestCase, app
from tests.unit.serve.support import issue_test_token


class TestTokenScopes(ServeRoutesTestCase):
    def test_user_token_follows_owner_role(self):
        headers = self._headers("carol")
        client = app.test_client()
        self._request_expecting_abort(
            "GET",
            "/factors/gpu",
            403,
            description="Insufficient permissions",
            headers=headers,
        )
        with self.db_connection() as conn:
            grant_operator(conn, "carol")
        self.assertEqual(client.get("/factors/gpu", headers=headers).status_code, 200)

    def test_user_token_limited_to_its_scopes(self):
        token = issue_test_token(self._site_ini, "alice", ["stats:read:own"])
        self._request_expecting_abort(
            "GET",
            "/roles",
            403,
            description="Token scope insufficient",
            headers={"Authorization": f"Bearer {token}"},
        )

    def test_user_token_of_previous_version_accepted(self):
        settings = load_serve_settings(serve_conf_defs(), self._site_ini)
        legacy = JWTManager.key(
            audience=settings.jwt.audience,
            algorithm=settings.jwt.algorithm,
            path=settings.jwt.key,
        )
        token = legacy.generate(AuthenticatedUser(login="alice", groups=[]), 1)
        client = app.test_client()
        resp = client.get("/roles", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 200)
