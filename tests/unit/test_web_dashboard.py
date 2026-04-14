"""Unit tests for Flask web dashboard script."""

from __future__ import annotations

import importlib.util
import json
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from tests.test_support import SlurmQuotaTestCase


_SCRIPT = Path(__file__).resolve().parent.parent.parent / "slurm-quota-web"


def _load_web_module():
    mod = sys.modules.get("slurm_quota_web")
    if mod is not None:
        return mod
    loader = SourceFileLoader("slurm_quota_web", str(_SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("Unable to create module spec for slurm_quota_web")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["slurm_quota_web"] = mod
    loader.exec_module(mod)
    return mod


class _FakeUrlopenResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _payload():
    return {
        "users": [
            {
                "username": "alice",
                "job_count": 2,
                "last_updated": "2024-06-01T10:00:00",
                "total_consumed_cpu_minutes": 120,
                "total_preallocated_cpu_minutes": 60,
                "quota_cpu_minutes": 600,
                "total_consumed_gpu_minutes": 0,
                "total_preallocated_gpu_minutes": 0,
                "quota_gpu_minutes": -1,
            }
        ],
        "accounts": [
            {
                "account": "hpc",
                "job_count": 3,
                "last_updated": "2024-06-01T10:00:00",
                "total_consumed_cpu_minutes": 100,
                "total_preallocated_cpu_minutes": 0,
                "quota_cpu_minutes": 500,
                "total_consumed_gpu_minutes": 0,
                "total_preallocated_gpu_minutes": 0,
                "quota_gpu_minutes": -1,
            }
        ],
    }


class TestWebDashboard(SlurmQuotaTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.web = _load_web_module()

    def test_exposes_wsgi_application_alias(self):
        self.assertTrue(hasattr(self.web, "app"))
        self.assertTrue(hasattr(self.web, "application"))
        self.assertIs(self.web.application, self.web.app)

    def test_dashboard_renders_user_and_account_rows(self):
        with patch.object(
            self.web, "urlopen", return_value=_FakeUrlopenResponse(_payload())
        ):
            client = self.web.app.test_client()
            resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("alice", body)
        self.assertIn("hpc", body)

    def test_forwards_username_filter_to_stats_api(self):
        with patch.object(
            self.web, "urlopen", return_value=_FakeUrlopenResponse(_payload())
        ) as m_urlopen:
            client = self.web.app.test_client()
            resp = client.get("/?username=alice")
        self.assertEqual(resp.status_code, 200)
        req = m_urlopen.call_args[0][0]
        self.assertIn("username=alice", req.full_url)

    def test_forwards_account_filter_to_stats_api(self):
        with patch.object(
            self.web, "urlopen", return_value=_FakeUrlopenResponse(_payload())
        ) as m_urlopen:
            client = self.web.app.test_client()
            resp = client.get("/?account=hpc")
        self.assertEqual(resp.status_code, 200)
        req = m_urlopen.call_args[0][0]
        self.assertIn("account=hpc", req.full_url)

    def test_username_and_account_are_mutually_exclusive(self):
        with patch.object(self.web, "urlopen") as m_urlopen:
            client = self.web.app.test_client()
            resp = client.get("/?username=alice&account=hpc")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("mutually exclusive", body)
        m_urlopen.assert_not_called()

    def test_urlerror_is_rendered_in_page(self):
        with patch.object(self.web, "urlopen", side_effect=URLError("boom")):
            client = self.web.app.test_client()
            resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("Failed to retrieve stats", body)

    def test_unit_hours_displays_decimal_hours(self):
        with patch.object(
            self.web, "urlopen", return_value=_FakeUrlopenResponse(_payload())
        ):
            client = self.web.app.test_client()
            resp = client.get("/?unit=hours")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("2.00", body)
        self.assertIn('name="unit"', body)
        self.assertIn('value="hours"', body)
