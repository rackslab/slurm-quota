"""Unit tests for slurm_quota.web."""

from __future__ import annotations

from unittest.mock import patch

from urllib.error import URLError

from slurm_quota import web

from tests.test_support import SlurmQuotaTestCase


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


def _stats_rows():
    users, accounts = _payload()["users"], _payload()["accounts"]
    return users, accounts


class TestWebDashboard(SlurmQuotaTestCase):
    def test_dashboard_renders_user_and_account_rows(self):
        with patch(
            "slurm_quota.client.fetch_stats_from_service",
            return_value=_stats_rows(),
        ):
            client = web.app.test_client()
            resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("alice", body)
        self.assertIn("hpc", body)

    def test_forwards_username_filter_to_stats_api(self):
        with patch(
            "slurm_quota.client.fetch_stats_from_service",
            return_value=_stats_rows(),
        ) as m_fetch:
            client = web.app.test_client()
            resp = client.get("/?username=alice")
        self.assertEqual(resp.status_code, 200)
        m_fetch.assert_called_once_with("alice", None, show_all=False)

    def test_forwards_account_filter_to_stats_api(self):
        with patch(
            "slurm_quota.client.fetch_stats_from_service",
            return_value=_stats_rows(),
        ) as m_fetch:
            client = web.app.test_client()
            resp = client.get("/?account=hpc")
        self.assertEqual(resp.status_code, 200)
        m_fetch.assert_called_once_with(None, "hpc", show_all=True)

    def test_username_and_account_are_mutually_exclusive(self):
        with patch("slurm_quota.client.fetch_stats_from_service") as m_fetch:
            client = web.app.test_client()
            resp = client.get("/?username=alice&account=hpc")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("mutually exclusive", body)
        m_fetch.assert_not_called()

    def test_urlerror_is_rendered_in_page(self):
        with patch(
            "slurm_quota.client.fetch_stats_from_service",
            side_effect=URLError("boom"),
        ):
            client = web.app.test_client()
            resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("Failed to retrieve stats", body)

    def test_unit_hours_displays_decimal_hours(self):
        with patch(
            "slurm_quota.client.fetch_stats_from_service",
            return_value=_stats_rows(),
        ):
            client = web.app.test_client()
            resp = client.get("/?unit=hours")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("2.00", body)
        self.assertIn('name="unit"', body)
        self.assertIn('value="hours"', body)
