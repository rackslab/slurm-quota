"""Shared helpers for slurm_quota.web unit tests."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

from slurm_quota import web


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
        {"username": "carol", "role": "manager"},
    ]


@contextmanager
def auth_disabled():
    with patch("slurm_quota.web.app.load_service_token", return_value="test-token"):
        with patch("slurm_quota.web.routes.current_role", return_value="admin"):
            yield


@contextmanager
def auth_enabled():
    yield


def configure_web_app() -> None:
    web.application.config["SECRET_KEY"] = "test-secret"


def extract_csrf(html: str) -> str:
    marker = 'name="_csrf" value="'
    start = html.index(marker) + len(marker)
    end = html.index('"', start)
    return html[start:end]
