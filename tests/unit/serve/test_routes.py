"""Unit tests for slurm_quota.serve.routes."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from rfl.authentication.errors import LDAPAuthenticationError
from rfl.authentication.user import AuthenticatedUser

from slurm_quota.database import init_database
from slurm_quota.serve.settings import conf_defs_path

from tests.test_support import SlurmQuotaTestCase
from tests.unit.serve.support import (
    issue_test_token,
    registered_app,
    write_ldap_site_ini,
    write_jwt_site_ini,
)

app = registered_app()


class TestRoutes(SlurmQuotaTestCase):
    def setUp(self):
        super().setUp()
        self._patch_slurm = patch("slurm_quota.serve.app.auth.require_slurm_user")
        self._patch_slurm.start()
        self.addCleanup(self._patch_slurm.stop)
        init_database()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._site_ini = write_jwt_site_ini(Path(self._tmpdir.name))
        app.setup(conf_defs_path(), self._site_ini)
        self._token = issue_test_token(self._site_ini)

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _enable_ldap_auth(self, tmp: Path) -> str:
        site_ini = write_ldap_site_ini(tmp)
        with patch("slurm_quota.serve.app.LDAPAuthentifier") as m_ldap_cls:
            m_ldap_cls.return_value.login.return_value = AuthenticatedUser(
                login="alice", groups=["users"]
            )
            app.setup(conf_defs_path(), site_ini)
        user = AuthenticatedUser(login="alice", groups=["users"])
        return app.jwt.generate(user, app.settings.jwt.duration)

    def test_health_returns_ok(self):
        client = app.test_client()
        resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.data), {"status": "ok"})

    def test_stats_returns_users_and_accounts(self):
        init_database()
        client = app.test_client()
        resp = client.get("/stats", headers=self._auth_headers())
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        self.assertIn("users", body)
        self.assertIn("accounts", body)

    def test_stats_supports_username_query_filter(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("alice", 120),
            )
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("bob", 30),
            )
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("hpc", 100),
            )
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("dev", 40),
            )
            conn.commit()

        with patch("slurm_quota.slurm.get_user_accounts", return_value={"hpc"}):
            client = app.test_client()
            resp = client.get("/stats?username=alice", headers=self._auth_headers())

        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        self.assertEqual(len(body["users"]), 1)
        self.assertEqual(body["users"][0]["username"], "alice")
        self.assertEqual(len(body["accounts"]), 1)
        self.assertEqual(body["accounts"][0]["account"], "hpc")

    def test_stats_supports_account_query_filter(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("alice", 120),
            )
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("hpc", 100),
            )
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("dev", 40),
            )
            conn.commit()

        client = app.test_client()
        resp = client.get("/stats?account=hpc", headers=self._auth_headers())
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        self.assertEqual(body["users"], [])
        self.assertEqual(len(body["accounts"]), 1)
        self.assertEqual(body["accounts"][0]["account"], "hpc")

    def test_stats_rejects_username_and_account_filters(self):
        init_database()
        client = app.test_client()
        resp = client.get(
            "/stats?username=alice&account=hpc",
            headers=self._auth_headers(),
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            json.loads(resp.data),
            {
                "error": "bad_request",
                "message": "username and account are mutually exclusive",
            },
        )

    def test_unknown_path_returns_not_found(self):
        client = app.test_client()
        resp = client.get("/does-not-exist")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(json.loads(resp.data), {"error": "not_found"})

    def test_stats_db_error_returns_500(self):
        init_database()
        with patch(
            "slurm_quota.serve.routes.sqlite3.connect",
            side_effect=sqlite3.Error("boom"),
        ):
            client = app.test_client()
            resp = client.get("/stats", headers=self._auth_headers())
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(json.loads(resp.data), {"error": "db_error"})

    def test_stats_rejects_missing_token(self):
        init_database()
        client = app.test_client()
        resp = client.get("/stats")
        self.assertEqual(resp.status_code, 403)
        body = json.loads(resp.data)
        self.assertEqual(body["error"], "forbidden")

    def test_stats_rejects_invalid_token(self):
        init_database()
        client = app.test_client()
        resp = client.get(
            "/stats",
            headers={"Authorization": "Bearer invalid"},
        )
        self.assertEqual(resp.status_code, 401)
        body = json.loads(resp.data)
        self.assertEqual(body["error"], "unauthorized")

    def test_auth_login_returns_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            token = self._enable_ldap_auth(Path(tmp))
            client = app.test_client()
            resp = client.post(
                "/login",
                json={"username": "alice", "password": "secret"},
            )
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        self.assertIn("token", body)
        self.assertIsInstance(token, str)

    def test_auth_login_invalid_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            site_ini = write_ldap_site_ini(Path(tmp))
            with patch("slurm_quota.serve.app.LDAPAuthentifier") as m_ldap_cls:
                m_ldap_cls.return_value.login.side_effect = LDAPAuthenticationError(
                    "Invalid user or password"
                )
                app.setup(conf_defs_path(), site_ini)
            client = app.test_client()
            resp = client.post(
                "/login",
                json={"username": "alice", "password": "wrong"},
            )
        self.assertEqual(resp.status_code, 401)

    def test_stats_with_token(self):
        init_database()
        client = app.test_client()
        resp = client.get("/stats", headers=self._auth_headers())
        self.assertEqual(resp.status_code, 200)

    def test_auth_login_not_found_for_jwt_method(self):
        client = app.test_client()
        resp = client.post(
            "/login",
            json={"username": "alice", "password": "secret"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_health_stays_public(self):
        client = app.test_client()
        resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.data), {"status": "ok"})
