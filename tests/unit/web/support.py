"""Shared helpers for slurm_quota.web unit tests."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from unittest.mock import patch
from urllib.parse import urlparse

from slurm_quota.web.app import SlurmQuotaWebApp
from slurm_quota.web.settings import SlurmQuotaWebSettings
from tests.test_support import web_assets_root

_app: SlurmQuotaWebApp | None = None


def registered_web_app() -> SlurmQuotaWebApp:
    settings = replace(
        SlurmQuotaWebSettings.from_os_environ(),
        assets_dir=web_assets_root(),
    )
    app = SlurmQuotaWebApp(settings)
    app.register()
    return app


def web_app() -> SlurmQuotaWebApp:
    global _app
    if _app is None:
        _app = registered_web_app()
    return _app


def payload() -> dict:
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


def stats_rows():
    data = payload()
    return data["users"], data["accounts"]


def roles_users() -> list[dict]:
    return [
        {"username": "alice", "role": "admin"},
        {"username": "bob", "role": "user"},
        {"username": "carol", "role": "manager", "accounts": ["hpc"]},
    ]


@contextmanager
def auth_disabled():
    with (
        patch("slurm_quota.web.app.ClientToken.load_value", return_value="test-token"),
        patch("slurm_quota.web.routes.current_role", return_value="admin"),
    ):
        yield


@contextmanager
def auth_enabled():
    yield


def configure_web_app() -> None:
    app = web_app()
    app.config["SECRET_KEY"] = "test-secret"
    app.secret_key = "test-secret"


def extract_csrf(html: str) -> str:
    marker = 'name="_csrf" value="'
    start = html.index(marker) + len(marker)
    end = html.index('"', start)
    return html[start:end]


def redirect_path(location: str) -> str:
    """Return the path from a redirect Location header.

    Werkzeug's Response.autocorrect_location_header rewrites relative
    Location values to absolute URLs (scheme + host + path). That flag
    defaulted to True before Werkzeug 2.1 and False since 2.1 (see pallets/
    werkzeug#2352). Old dev stacks may therefore see http://localhost/quota/
    while newer dev stacks see /quota/ for the same redirect.

    For this reason, we compare path only, not the full URL.
    """
    return urlparse(location).path
