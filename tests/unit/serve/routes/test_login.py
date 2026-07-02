"""Unit tests for POST /login."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from rfl.authentication.errors import LDAPAuthenticationError
from rfl.authentication.user import AuthenticatedUser

from tests.test_support import serve_conf_defs
from tests.unit.serve.routes.base import ServeRoutesTestCase, app
from tests.unit.serve.support import write_ldap_site_ini


class TestLoginRoute(ServeRoutesTestCase):
    def _enable_ldap_auth(self, tmp: Path) -> str:
        site_ini = write_ldap_site_ini(tmp)
        with patch("slurm_quota.serve.app.LDAPAuthentifier") as m_ldap_cls:
            m_ldap_cls.return_value.login.return_value = AuthenticatedUser(
                login="alice", groups=["users"]
            )
            app.setup(serve_conf_defs(), site_ini)
        user = AuthenticatedUser(login="alice", groups=["users"])
        return app.jwt.generate(user, app.settings.jwt.duration)

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
                app.setup(serve_conf_defs(), site_ini)
            client = app.test_client()
            resp = client.post(
                "/login",
                json={"username": "alice", "password": "wrong"},
            )
        self.assertEqual(resp.status_code, 401)

    def test_auth_login_not_found_for_jwt_method(self):
        client = app.test_client()
        resp = client.post(
            "/login",
            json={"username": "alice", "password": "secret"},
        )
        self.assertEqual(resp.status_code, 404)
