"""Shared helpers for functional CLI tests (no TestCase here — use per-command modules)."""

from __future__ import annotations

import io
import json
import sys
from contextlib import contextmanager, redirect_stdout
from unittest.mock import patch

from tests.test_support import SlurmQuotaTestCase


class FakeJsonUrlopenResponse:
    def __init__(self, payload):
        self.status = 200
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FunctionalCLIBase(SlurmQuotaTestCase):
    """Base for one-command functional TestCase classes."""

    def run_main(self, argv):
        with patch.object(sys, "argv", argv):
            self.sq.main()

    def run_main_exit(self, argv, code=1):
        with patch.object(sys, "argv", argv):
            with self.assertRaises(SystemExit) as cm:
                self.sq.main()
        self.assertEqual(cm.exception.code, code)

    @contextmanager
    def capture_stdout(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            yield buf

    @staticmethod
    def stats_json_payload():
        return {
            "users": [
                {
                    "username": "alice",
                    "job_count": 0,
                    "last_updated": "2024-06-01T10:00:00",
                    "total_consumed_cpu_minutes": 1,
                    "total_preallocated_cpu_minutes": 0,
                    "quota_cpu_minutes": -1,
                    "total_consumed_gpu_minutes": 0,
                    "total_preallocated_gpu_minutes": 0,
                    "quota_gpu_minutes": -1,
                }
            ],
            "accounts": [
                {
                    "account": "acct1",
                    "job_count": 0,
                    "last_updated": "2024-06-01T10:00:00",
                    "total_consumed_cpu_minutes": 1,
                    "total_preallocated_cpu_minutes": 0,
                    "quota_cpu_minutes": -1,
                    "total_consumed_gpu_minutes": 0,
                    "total_preallocated_gpu_minutes": 0,
                    "quota_gpu_minutes": -1,
                }
            ],
        }
