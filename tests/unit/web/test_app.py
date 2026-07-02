"""Unit tests for slurm_quota.web.app."""

from __future__ import annotations

from unittest.mock import patch

from tests.test_support import SlurmQuotaTestCase
from tests.unit.web.support import configure_web_app, web_app


class TestSlurmQuotaWebApp(SlurmQuotaTestCase):
    def setUp(self):
        super().setUp()
        configure_web_app()

    def test_dashboard_redirects_to_login_without_session(self):
        client = web_app().test_client()
        resp = client.get("/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_missing_session_key_shows_error_page(self):
        web_app().secret_key = None
        client = web_app().test_client()
        resp = client.get("/")
        self.assertEqual(resp.status_code, 503)
        body = resp.get_data(as_text=True)
        self.assertIn("administrator", body)
        self.assertIn("server logs", body)

    def test_env_token_bypasses_login(self):
        with (
            patch("slurm_quota.web.app.load_service_token", return_value="env-jwt"),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            m_client.return_value.stats.return_value = ([], [])
            client = web_app().test_client()
            resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
