"""Unit tests for /tokens endpoints and token scopes enforcement."""

from __future__ import annotations

import json
import time

from rfl.authentication.jwt import JWTManager
from rfl.authentication.user import AuthenticatedUser

from slurm_quota.database import get_app_token, grant_operator, revoke_operator
from slurm_quota.serve.settings import load_serve_settings
from tests.test_support import serve_conf_defs
from tests.unit.serve.routes.base import ServeRoutesTestCase, app
from tests.unit.serve.support import issue_test_app_token, issue_test_token


class TokensTestCase(ServeRoutesTestCase):
    def _app_headers(
        self, username: str, scopes: list[str], name: str = "app"
    ) -> tuple[dict[str, str], str]:
        token, token_id = issue_test_app_token(
            self._site_ini, username, scopes, name=name
        )
        return {"Authorization": f"Bearer {token}"}, token_id

    def _create(self, username: str = "bob", **payload):
        client = app.test_client()
        return client.post("/tokens", json=payload, headers=self._headers(username))


class TestCreateTokenRoute(TokensTestCase):
    def test_user_creates_application_token(self):
        before = int(time.time())
        resp = self._create(name="ci", scopes=["stats:read"], duration=200)
        self.assertEqual(resp.status_code, 201)
        body = json.loads(resp.data)
        self.assertEqual(body["name"], "ci")
        self.assertEqual(body["username"], "bob")
        self.assertEqual(body["scopes"], ["stats:read"])
        self.assertEqual(body["status"], "active")
        self.assertGreaterEqual(body["expires_at"], before + 200 * 86400)

        decoded = app.jwt.decode(body["token"])
        self.assertEqual(decoded.login, "bob")
        self.assertEqual(decoded.kind, "application")
        self.assertEqual(decoded.jti, body["id"])
        self.assertEqual(decoded.scopes, {"stats:read"})

        with self.db_connection() as conn:
            stored = get_app_token(conn, body["id"])
        assert stored is not None
        self.assertEqual(stored["name"], "ci")
        self.assertNotIn(body["token"], json.dumps(stored))

    def test_default_duration(self):
        before = int(time.time())
        resp = self._create(name="ci", scopes=["stats:read"])
        self.assertEqual(resp.status_code, 201)
        body = json.loads(resp.data)
        self.assertGreaterEqual(body["expires_at"], before + 90 * 86400)
        self.assertLessEqual(body["expires_at"], int(time.time()) + 90 * 86400)

    def test_operator_creates_token_with_quotas_scopes(self):
        with self.db_connection() as conn:
            grant_operator(conn, "carol")
        resp = self._create(
            "carol", name="ci", scopes=["quotas:write", "stats:read", "quotas:read"]
        )
        self.assertEqual(resp.status_code, 201)
        body = json.loads(resp.data)
        self.assertEqual(body["scopes"], ["stats:read", "quotas:read", "quotas:write"])

    def test_rejects_scope_not_allowed_for_role(self):
        self._request_expecting_abort(
            "POST",
            "/tokens",
            403,
            description="Scopes not allowed for user: quotas:write",
            json={"name": "ci", "scopes": ["stats:read", "quotas:write"]},
            headers=self._headers("bob"),
        )

    def test_rejects_unknown_scope(self):
        self._request_expecting_abort(
            "POST",
            "/tokens",
            400,
            description="Unknown scopes: foo",
            json={"name": "ci", "scopes": ["foo"]},
            headers=self._headers("bob"),
        )

    def test_rejects_empty_scopes(self):
        self._request_expecting_abort(
            "POST",
            "/tokens",
            400,
            description="scopes must be a non-empty list of strings",
            json={"name": "ci", "scopes": []},
            headers=self._headers("bob"),
        )

    def test_rejects_duration_above_maximum(self):
        self._request_expecting_abort(
            "POST",
            "/tokens",
            400,
            description="duration must be at most 365 days",
            json={"name": "ci", "scopes": ["stats:read"], "duration": 366},
            headers=self._headers("bob"),
        )

    def test_rejects_invalid_duration(self):
        for duration in (0, -1, "10", True):
            with self.subTest(duration=duration):
                self._request_expecting_abort(
                    "POST",
                    "/tokens",
                    400,
                    description="duration must be a positive integer",
                    json={"name": "ci", "scopes": ["stats:read"], "duration": duration},
                    headers=self._headers("bob"),
                )

    def test_rejects_invalid_name(self):
        for name in ("", "a b", "a/b", 1):
            with self.subTest(name=name):
                self._request_expecting_abort(
                    "POST",
                    "/tokens",
                    400,
                    description="Invalid token name",
                    json={"name": name, "scopes": ["stats:read"]},
                    headers=self._headers("bob"),
                )

    def test_rejects_duplicate_active_name(self):
        self.assertEqual(
            self._create(name="ci", scopes=["stats:read"]).status_code, 201
        )
        self._request_expecting_abort(
            "POST",
            "/tokens",
            400,
            description="An active token named ci already exists",
            json={"name": "ci", "scopes": ["stats:read"]},
            headers=self._headers("bob"),
        )

    def test_accepts_name_of_revoked_token(self):
        body = json.loads(self._create(name="ci", scopes=["stats:read"]).data)
        client = app.test_client()
        resp = client.delete(f"/tokens/{body['id']}", headers=self._headers("bob"))
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(
            self._create(name="ci", scopes=["stats:read"]).status_code, 201
        )

    def test_application_token_cannot_create_token(self):
        headers, _ = self._app_headers("bob", ["stats:read"])
        self._request_expecting_abort(
            "POST",
            "/tokens",
            403,
            description="Application tokens cannot manage tokens",
            json={"name": "ci", "scopes": ["stats:read"]},
            headers=headers,
        )


class TestListTokensRoute(TokensTestCase):
    def test_user_lists_own_tokens(self):
        self._create("bob", name="ci", scopes=["stats:read"])
        self._create("dave", name="other", scopes=["stats:read"])
        client = app.test_client()
        resp = client.get("/tokens", headers=self._headers("bob"))
        self.assertEqual(resp.status_code, 200)
        tokens = json.loads(resp.data)["tokens"]
        self.assertEqual([token["name"] for token in tokens], ["ci"])
        self.assertEqual(tokens[0]["status"], "active")
        self.assertNotIn("token", tokens[0])

    def test_lists_revoked_and_expired_status(self):
        _, revoked_id = self._app_headers("bob", ["stats:read"], name="revoked")
        _, expired_id = self._app_headers("bob", ["stats:read"], name="expired")
        with self.db_connection() as conn:
            conn.execute(
                "UPDATE app_tokens SET revoked_at = 1 WHERE id = ?", (revoked_id,)
            )
            conn.execute(
                "UPDATE app_tokens SET expires_at = 1 WHERE id = ?", (expired_id,)
            )
            conn.commit()
        client = app.test_client()
        resp = client.get("/tokens", headers=self._headers("bob"))
        statuses = {
            token["name"]: token["status"] for token in json.loads(resp.data)["tokens"]
        }
        self.assertEqual(statuses, {"revoked": "revoked", "expired": "expired"})

    def test_admin_lists_tokens_of_all_users_or_one_user(self):
        self._create("bob", name="ci", scopes=["stats:read"])
        self._create("dave", name="other", scopes=["stats:read"])
        client = app.test_client()
        resp = client.get("/tokens?all=1", headers=self._headers("alice"))
        names = [token["name"] for token in json.loads(resp.data)["tokens"]]
        self.assertEqual(names, ["ci", "other"])
        resp = client.get("/tokens?username=dave", headers=self._headers("alice"))
        names = [token["name"] for token in json.loads(resp.data)["tokens"]]
        self.assertEqual(names, ["other"])

    def test_non_admin_cannot_list_tokens_of_other_users(self):
        self._request_expecting_abort(
            "GET",
            "/tokens?username=dave",
            403,
            description="Not allowed to view tokens of other users",
            headers=self._headers("bob"),
        )

    def test_application_token_cannot_list_tokens(self):
        headers, _ = self._app_headers("bob", ["stats:read"])
        self._request_expecting_abort(
            "GET",
            "/tokens",
            403,
            description="Application tokens cannot manage tokens",
            headers=headers,
        )


class TestRevokeTokenRoute(TokensTestCase):
    def test_owner_revokes_token(self):
        headers, token_id = self._app_headers("bob", ["stats:read"])
        client = app.test_client()
        self.assertEqual(client.get("/me", headers=headers).status_code, 200)
        resp = client.delete(f"/tokens/{token_id}", headers=self._headers("bob"))
        self.assertEqual(resp.status_code, 204)
        self._request_expecting_abort(
            "GET", "/me", 401, description="Token is revoked", headers=headers
        )

    def test_admin_revokes_token_of_other_user(self):
        _, token_id = self._app_headers("bob", ["stats:read"])
        client = app.test_client()
        resp = client.delete(f"/tokens/{token_id}", headers=self._headers("alice"))
        self.assertEqual(resp.status_code, 204)
        with self.db_connection() as conn:
            token = get_app_token(conn, token_id)
        assert token is not None
        self.assertIsNotNone(token["revoked_at"])

    def test_user_cannot_revoke_token_of_other_user(self):
        _, token_id = self._app_headers("bob", ["stats:read"])
        self._request_expecting_abort(
            "DELETE",
            f"/tokens/{token_id}",
            404,
            description="Token not found",
            headers=self._headers("dave"),
        )

    def test_unknown_token(self):
        self._request_expecting_abort(
            "DELETE",
            "/tokens/unknown",
            404,
            description="Token not found",
            headers=self._headers("bob"),
        )


class TestTokenScopes(TokensTestCase):
    def test_application_token_limited_to_its_scopes(self):
        headers, _ = self._app_headers("alice", ["stats:read"])
        client = app.test_client()
        self.assertEqual(client.get("/stats", headers=headers).status_code, 200)
        self._request_expecting_abort(
            "GET",
            "/quotas/defaults",
            403,
            description="Token scope insufficient",
            headers=headers,
        )

    def test_application_token_with_write_scope(self):
        headers, _ = self._app_headers("alice", ["quotas:write"])
        client = app.test_client()
        resp = client.put("/factors/gpu/a100", json={"factor": 2}, headers=headers)
        self.assertEqual(resp.status_code, 204)

    def test_application_token_unknown_in_database(self):
        settings = load_serve_settings(serve_conf_defs(), self._site_ini)
        token = app.jwt.generate_application(
            AuthenticatedUser(login="alice", groups=[]),
            settings.jwt.app_duration,
            "missing",
            ["stats:read"],
        )
        self._request_expecting_abort(
            "GET",
            "/me",
            401,
            description="Token is unknown",
            headers={"Authorization": f"Bearer {token}"},
        )

    def test_application_token_follows_owner_role(self):
        with self.db_connection() as conn:
            grant_operator(conn, "carol")
        headers, _ = self._app_headers("carol", ["quotas:read"])
        client = app.test_client()
        self.assertEqual(client.get("/factors/gpu", headers=headers).status_code, 200)
        with self.db_connection() as conn:
            revoke_operator(conn, "carol")
        self._request_expecting_abort(
            "GET",
            "/factors/gpu",
            403,
            description="Insufficient permissions",
            headers=headers,
        )

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
        token = issue_test_token(self._site_ini, "alice", ["stats:read"])
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

    def test_application_token_does_not_follow_owner_promotion(self):
        headers, _ = self._app_headers("carol", ["stats:read"])
        with self.db_connection() as conn:
            grant_operator(conn, "carol")
        self._request_expecting_abort(
            "GET",
            "/factors/gpu",
            403,
            description="Token scope insufficient",
            headers=headers,
        )
