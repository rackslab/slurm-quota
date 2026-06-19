"""Unit tests for slurm_quota.web.routes."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from urllib.error import URLError

from slurm_quota import web
from slurm_quota.client import ServiceHTTPError

from tests.test_support import SlurmQuotaTestCase
from tests.unit.web.support import (
    auth_disabled,
    auth_enabled,
    configure_web_app,
    extract_csrf,
    stats_rows,
)


class TestDashboardRoutes(SlurmQuotaTestCase):
    def setUp(self):
        super().setUp()
        configure_web_app()

    def test_dashboard_renders_user_and_account_rows(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.stats.return_value = stats_rows()
            client = web.application.test_client()
            resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("alice", body)
        self.assertIn("hpc", body)

    def test_forwards_username_filter_to_stats_api(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.stats.return_value = stats_rows()
            client = web.application.test_client()
            resp = client.get("/?username=alice")
        self.assertEqual(resp.status_code, 200)
        m_client.return_value.stats.assert_called_once_with(
            "alice", None, show_all=False
        )

    def test_forwards_account_filter_to_stats_api(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.stats.return_value = stats_rows()
            client = web.application.test_client()
            resp = client.get("/?account=hpc")
        self.assertEqual(resp.status_code, 200)
        m_client.return_value.stats.assert_called_once_with(None, "hpc", show_all=True)

    def test_username_and_account_are_mutually_exclusive(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            client = web.application.test_client()
            resp = client.get("/?username=alice&account=hpc")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("mutually exclusive", body)
        m_client.return_value.stats.assert_not_called()

    def test_urlerror_is_rendered_in_page(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.stats.side_effect = URLError("boom")
            client = web.application.test_client()
            resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("Failed to retrieve stats", body)

    def test_unit_hours_displays_decimal_hours(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.stats.return_value = stats_rows()
            client = web.application.test_client()
            resp = client.get("/?unit=hours")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("2.00", body)
        self.assertIn('name="unit"', body)
        self.assertIn('value="hours"', body)


class TestAuthRoutes(SlurmQuotaTestCase):
    def setUp(self):
        super().setUp()
        configure_web_app()

    def _mock_login_client(self) -> MagicMock:
        mock_api = MagicMock()

        def _login(username, password):
            mock_api.token = "jwt-token"
            return "jwt-token"

        mock_api.login.side_effect = _login
        return mock_api

    def test_login_post_api_unreachable_shows_error_page(self):
        with patch("slurm_quota.web.routes.APIClient") as m_client_cls:
            m_client_cls.return_value.login.side_effect = URLError("boom")
            client = web.application.test_client()
            login_page = client.get("/login")
            csrf = extract_csrf(login_page.get_data(as_text=True))
            resp = client.post(
                "/login",
                data={
                    "_csrf": csrf,
                    "username": "alice",
                    "password": "secret",
                },
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("Login failed", body)
        self.assertIn("boom", body)

    def test_login_success_sets_session_and_renders_dashboard(self):
        mock_api = self._mock_login_client()
        with (
            auth_enabled(),
            patch("slurm_quota.web.routes.APIClient", return_value=mock_api),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            m_client.return_value.stats.return_value = stats_rows()
            client = web.application.test_client()
            login_page = client.get("/login")
            csrf = extract_csrf(login_page.get_data(as_text=True))
            resp = client.post(
                "/login",
                data={
                    "_csrf": csrf,
                    "username": "alice",
                    "password": "secret",
                    "next": "/",
                },
                follow_redirects=False,
            )
            self.assertEqual(resp.status_code, 302)
            dashboard = client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("Signed in as", dashboard.get_data(as_text=True))
        m_client.return_value.stats.assert_called_with(None, None, show_all=True)

    def test_login_rejects_bad_credentials(self):
        mock_api = MagicMock()
        mock_api.login.side_effect = ServiceHTTPError(401)
        with (
            auth_enabled(),
            patch("slurm_quota.web.routes.APIClient", return_value=mock_api),
        ):
            client = web.application.test_client()
            login_page = client.get("/login")
            csrf = extract_csrf(login_page.get_data(as_text=True))
            resp = client.post(
                "/login",
                data={
                    "_csrf": csrf,
                    "username": "alice",
                    "password": "wrong",
                    "next": "/",
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Invalid username or password", resp.get_data(as_text=True))

    def test_logout_clears_session(self):
        mock_api = self._mock_login_client()
        with (
            auth_enabled(),
            patch("slurm_quota.web.routes.APIClient", return_value=mock_api),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            m_client.return_value.stats.return_value = stats_rows()
            client = web.application.test_client()
            login_page = client.get("/login")
            csrf = extract_csrf(login_page.get_data(as_text=True))
            client.post(
                "/login",
                data={
                    "_csrf": csrf,
                    "username": "alice",
                    "password": "secret",
                    "next": "/",
                },
            )
            dashboard = client.get("/")
            csrf = extract_csrf(dashboard.get_data(as_text=True))
            logout = client.post(
                "/logout",
                data={"_csrf": csrf},
                follow_redirects=False,
            )
            self.assertEqual(logout.status_code, 302)
            redirect = client.get("/")
        self.assertEqual(redirect.status_code, 302)
        self.assertIn("/login", redirect.headers["Location"])

    def test_expired_token_redirects_to_login(self):
        mock_api = self._mock_login_client()
        with (
            auth_enabled(),
            patch("slurm_quota.web.routes.APIClient", return_value=mock_api),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            m_client.return_value.stats.side_effect = ServiceHTTPError(401)
            client = web.application.test_client()
            login_page = client.get("/login")
            csrf = extract_csrf(login_page.get_data(as_text=True))
            client.post(
                "/login",
                data={
                    "_csrf": csrf,
                    "username": "alice",
                    "password": "secret",
                    "next": "/",
                },
            )
            resp = client.get("/", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("session_expired", resp.headers["Location"])
