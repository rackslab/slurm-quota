"""Shared helpers for functional CLI tests (no TestCase here — use per-command modules)."""

from __future__ import annotations

import io
import json
import sys
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlparse

from slurm_quota.cli import main as cli_main
from slurm_quota.charge import main as charge_main
from slurm_quota.serve.cli import main as serve_main
from slurm_quota.serve.prune import main as prune_main
from slurm_quota.serve.token import main as token_main
from slurm_quota.token import save_service_token

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


class FakeNoContentUrlopenResponse:
    def __init__(self, *, status: int = 204):
        self.status = status

    def read(self):
        return b""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FunctionalCLIBase(SlurmQuotaTestCase):
    """Base for one-command functional TestCase classes."""

    def run_cli_main(self, argv):
        with patch.object(sys, "argv", argv):
            cli_main()

    def run_charge_main(self, argv):
        with patch.object(sys, "argv", argv):
            charge_main()

    def run_prune_main(self, argv):
        with patch.object(sys, "argv", argv):
            prune_main()

    def run_serve_main(self, argv):
        with patch.object(sys, "argv", argv):
            serve_main()

    def run_token_main(self, argv):
        with patch.object(sys, "argv", argv):
            token_main()

    def run_cli_main_exit(self, argv, code=1):
        with patch.object(sys, "argv", argv):
            with self.assertRaises(SystemExit) as cm:
                cli_main()
        self.assertEqual(cm.exception.code, code)

    def run_token_main_exit(self, argv, code=1):
        with patch.object(sys, "argv", argv):
            with self.assertRaises(SystemExit) as cm:
                token_main()
        self.assertEqual(cm.exception.code, code)

    @contextmanager
    def capture_stdout(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            yield buf

    def update_settings(self, **values: int) -> None:
        """Set ``settings`` rows (requires ``init_database()`` so the table exists)."""
        with self.db_connection() as conn:
            for key, value in values.items():
                conn.execute(
                    "UPDATE settings SET value = ? WHERE key = ?",
                    (str(value), key),
                )
            conn.commit()

    @staticmethod
    def stats_json_payload() -> Dict[str, List[Dict[str, Any]]]:
        """Full multi-user / multi-account payload (no ``?username=`` filter)."""
        return stats_payload_full()

    @staticmethod
    def stats_urlopen_side_effect(request: Any) -> FakeJsonUrlopenResponse:
        """
        Return JSON like the real ``/stats`` handler: full list without query param,
        or users/accounts filtered from ``_STATS_REST_PAYLOAD`` when
        ``?username=`` and/or ``?account=`` are present.
        """
        qs = parse_qs(urlparse(request.full_url).query)
        raw_username = qs.get("username", [None])[0]
        raw_account = qs.get("account", [None])[0]
        username: Optional[str] = unquote(raw_username) if raw_username else None
        account: Optional[str] = unquote(raw_account) if raw_account else None
        payload = _stats_payload_filtered(username, account)
        return FakeJsonUrlopenResponse(payload)


class FunctionalAPICliBase(FunctionalCLIBase):
    """Base for functional tests that call the HTTP API with a saved JWT."""

    def setUp(self):
        super().setUp()
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env({"XDG_CONFIG_HOME": str(config_home)})
        save_service_token("saved-jwt")


def _stats_user_row(
    name: str,
    consumed_cpu: int,
    *,
    job_count: int = 0,
    prealloc_cpu: int = 0,
    quota_cpu: int = -1,
    consumed_gpu: int = 0,
    prealloc_gpu: int = 0,
    quota_gpu: int = -1,
) -> Dict[str, Any]:
    return {
        "username": name,
        "job_count": job_count,
        "last_updated": "2024-06-01T10:00:00",
        "total_consumed_cpu_minutes": consumed_cpu,
        "total_preallocated_cpu_minutes": prealloc_cpu,
        "quota_cpu_minutes": quota_cpu,
        "total_consumed_gpu_minutes": consumed_gpu,
        "total_preallocated_gpu_minutes": prealloc_gpu,
        "quota_gpu_minutes": quota_gpu,
    }


def _stats_account_row(
    name: str,
    consumed_cpu: int,
    *,
    job_count: int = 0,
    prealloc_cpu: int = 0,
    quota_cpu: int = -1,
    consumed_gpu: int = 0,
    prealloc_gpu: int = 0,
    quota_gpu: int = -1,
) -> Dict[str, Any]:
    return {
        "account": name,
        "job_count": job_count,
        "last_updated": "2024-06-01T10:00:00",
        "total_consumed_cpu_minutes": consumed_cpu,
        "total_preallocated_cpu_minutes": prealloc_cpu,
        "quota_cpu_minutes": quota_cpu,
        "total_consumed_gpu_minutes": consumed_gpu,
        "total_preallocated_gpu_minutes": prealloc_gpu,
        "quota_gpu_minutes": quota_gpu,
    }


# Single canonical REST body for functional ``urlopen`` mocks (all users and accounts).
_STATS_REST_PAYLOAD: Dict[str, List[Dict[str, Any]]] = {
    "users": [
        _stats_user_row("alice", 120, job_count=2, prealloc_cpu=60, quota_cpu=600),
        _stats_user_row("bob", 30, job_count=1),
    ],
    "accounts": [
        _stats_account_row("hpc", 100, job_count=3, quota_cpu=500),
        _stats_account_row("dev", 40, job_count=0),
    ],
}

# Which accounts appear when ``GET /stats?username=`` is set (like ``get_user_accounts``).
_STATS_ACCOUNT_NAMES_BY_USER: Dict[str, frozenset[str]] = {
    "alice": frozenset({"hpc"}),
    "bob": frozenset({"dev"}),
}


def _stats_payload_filtered_for_user(username: str) -> Dict[str, List[Dict[str, Any]]]:
    names = _STATS_ACCOUNT_NAMES_BY_USER.get(username)
    if not names:
        return {"users": [], "accounts": []}
    users = [u for u in _STATS_REST_PAYLOAD["users"] if u["username"] == username]
    accounts = [a for a in _STATS_REST_PAYLOAD["accounts"] if a["account"] in names]
    return {"users": users, "accounts": accounts}


def _stats_payload_filtered(
    username: Optional[str], account: Optional[str]
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Return the shared multi-user / multi-account stats body (filtered) by mimicking the real
    ``/stats`` handler.
    """
    if username is None and account is None:
        return _STATS_REST_PAYLOAD

    users: List[Dict[str, Any]]
    accounts_filter: Optional[set[str]] = None
    if username is None:
        users = _STATS_REST_PAYLOAD["users"]
    else:
        by_user = _stats_payload_filtered_for_user(username)
        users = by_user["users"]
        accounts_filter = {item["account"] for item in by_user["accounts"]}

    if account:
        requested = {account}
        if accounts_filter is None:
            accounts_filter = requested
        else:
            accounts_filter = accounts_filter.intersection(requested)
        users = []

    if accounts_filter is None:
        accounts = _STATS_REST_PAYLOAD["accounts"]
    else:
        accounts = [
            item
            for item in _STATS_REST_PAYLOAD["accounts"]
            if item["account"] in accounts_filter
        ]
    return {"users": users, "accounts": accounts}


def stats_payload_full() -> Dict[str, List[Dict[str, Any]]]:
    """Return the shared multi-user / multi-account stats body (unfiltered)."""
    return _STATS_REST_PAYLOAD
