"""Unit tests for fetch_stats_from_service."""

from __future__ import annotations

import json
from unittest.mock import patch

from urllib.error import URLError

from tests.test_support import SlurmQuotaTestCase


class _FakeUrlopenResponse:
    def __init__(self, payload=None, status=200):
        self.status = status
        self._payload = payload if payload is not None else {}

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _sample_payload():
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
                "account": "acct1",
                "job_count": 0,
                "last_updated": None,
                "total_consumed_cpu_minutes": 1,
                "total_preallocated_cpu_minutes": 0,
                "quota_cpu_minutes": -1,
                "total_consumed_gpu_minutes": 0,
                "total_preallocated_gpu_minutes": 0,
                "quota_gpu_minutes": -1,
            }
        ],
    }


class TestFetchStatsFromService(SlurmQuotaTestCase):
    def test_returns_users_and_accounts(self):
        payload = _sample_payload()
        with patch.object(
            self.sq, "urlopen", return_value=_FakeUrlopenResponse(payload)
        ):
            users, accounts = self.sq.fetch_stats_from_service("alice", False)
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["username"], "alice")
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["account"], "acct1")

    def test_empty_payload_lists_when_keys_missing(self):
        with patch.object(self.sq, "urlopen", return_value=_FakeUrlopenResponse({})):
            users, accounts = self.sq.fetch_stats_from_service(None, True)
        self.assertEqual(users, [])
        self.assertEqual(accounts, [])

    def test_adds_username_query_when_filtered(self):
        with patch.object(
            self.sq, "urlopen", return_value=_FakeUrlopenResponse(_sample_payload())
        ) as m_urlopen:
            self.sq.fetch_stats_from_service("alice", False)
        req = m_urlopen.call_args[0][0]
        self.assertIn("username=alice", req.full_url)

    def test_no_username_query_when_show_all(self):
        with patch.object(
            self.sq, "urlopen", return_value=_FakeUrlopenResponse(_sample_payload())
        ) as m_urlopen:
            self.sq.fetch_stats_from_service("alice", True)
        req = m_urlopen.call_args[0][0]
        self.assertNotIn("username=", req.full_url)

    def test_respects_slurm_quota_url(self):
        self.env({"SLURM_QUOTA_URL": "http://custom.example:9999/api/"})
        with patch.object(
            self.sq, "urlopen", return_value=_FakeUrlopenResponse(_sample_payload())
        ) as m_urlopen:
            self.sq.fetch_stats_from_service(None, True)
        req = m_urlopen.call_args[0][0]
        self.assertTrue(
            req.full_url.startswith("http://custom.example:9999/api/"),
            req.full_url,
        )
        self.assertIn("/stats", req.full_url)

    def test_raises_stats_http_error_on_bad_status(self):
        with patch.object(
            self.sq, "urlopen", return_value=_FakeUrlopenResponse({}, status=500)
        ):
            with self.assertRaises(self.sq.StatsHTTPError) as cm:
                self.sq.fetch_stats_from_service(None, True)
        self.assertEqual(cm.exception.status, 500)

    def test_urlerror_propagates(self):
        with patch.object(self.sq, "urlopen", side_effect=URLError("boom")):
            with self.assertRaises(URLError):
                self.sq.fetch_stats_from_service(None, True)
