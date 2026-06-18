"""Unit tests for slurm_quota.web.app."""

from __future__ import annotations

from unittest.mock import patch

from slurm_quota import web

from tests.test_support import SlurmQuotaTestCase
from tests.unit.web.support import configure_web_app


class TestSlurmQuotaWebApp(SlurmQuotaTestCase):
    def setUp(self):
        super().setUp()
        configure_web_app()

    def test_dashboard_redirects_to_login_when_auth_required(self):
        with patch.object(web.app, "auth_required_cached", return_value=True):
            client = web.app.test_client()
            resp = client.get("/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_missing_session_key_shows_error_page(self):
        with patch.object(web.app, "auth_required_cached", return_value=True):
            web.app.secret_key = None
            client = web.app.test_client()
            resp = client.get("/")
        self.assertEqual(resp.status_code, 503)
        body = resp.get_data(as_text=True)
        self.assertIn("administrator", body)
        self.assertIn("server logs", body)
