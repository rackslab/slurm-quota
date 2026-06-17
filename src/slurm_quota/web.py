# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Flask WSGI dashboard for slurm-quota stats."""

from __future__ import annotations

import os
import site
import sysconfig
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import URLError

from flask import Flask, render_template, request

from slurm_quota import client as stats_client
from slurm_quota.client import ServiceHTTPError


def _assets_root() -> Path:
    """Return the directory containing templates/ and static/ for the dashboard.

    Resolution order:
    1. SLURM_QUOTA_WEB_ASSETS_DIR when set (custom layout).
    2. Repo-root web/ when running from a git checkout (src/slurm_quota/web.py).
    3. Install prefix data directory (slurm-quota/web under sysconfig data path),
       used by normal pip install into a virtualenv or system prefix.
    4. User site-packages install (~/.local/slurm-quota/web) when the package
       is loaded from a pip install --user environment.
    5. /usr/share/slurm-quota/web as the default fallback for distro packages.
    """
    env_root = os.environ.get("SLURM_QUOTA_WEB_ASSETS_DIR")
    if env_root:
        return Path(env_root)

    repo_web = Path(__file__).resolve().parents[2] / "web"
    if repo_web.is_dir():
        return repo_web

    data_root = Path(sysconfig.get_path("data")) / "slurm-quota" / "web"
    if (data_root / "templates").is_dir():
        return data_root

    user_site = site.getusersitepackages()
    if user_site and str(Path(__file__).resolve()).startswith(user_site):
        user_data = Path(site.getuserbase()) / "slurm-quota" / "web"
        if (user_data / "templates").is_dir():
            return user_data

    return Path("/usr/share/slurm-quota/web")


_WEB_ROOT = _assets_root()
app = Flask(
    __name__,
    template_folder=str(_WEB_ROOT / "templates"),
    static_folder=str(_WEB_ROOT / "static"),
)


def _parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _format_minutes(value_minutes: int, display_hours: bool) -> str:
    if display_hours:
        return f"{value_minutes / 60:.2f}"
    return str(value_minutes)


def _quota_label(quota: int, display_hours: bool) -> str:
    if quota < 0:
        return "∞"
    return _format_minutes(quota, display_hours)


def _usage_percent(consumed: int, preallocated: int, quota: int) -> Optional[float]:
    if quota <= 0:
        return None
    return min(((consumed + preallocated) / quota) * 100.0, 100.0)


def _status_class(percent: Optional[float]) -> str:
    if percent is None:
        return "bar-unlimited"
    if percent >= 95.0:
        return "bar-danger"
    if percent >= 80.0:
        return "bar-warning"
    return "bar-ok"


def _decorate_rows(
    rows: List[Dict[str, Any]], name_key: str, display_hours: bool
) -> List[Dict[str, Any]]:
    decorated: List[Dict[str, Any]] = []
    for item in rows:
        cpu_consumed = _parse_int(item.get("total_consumed_cpu_minutes"))
        cpu_preallocated = _parse_int(item.get("total_preallocated_cpu_minutes"))
        cpu_quota = _parse_int(item.get("quota_cpu_minutes"), -1)
        cpu_percent = _usage_percent(cpu_consumed, cpu_preallocated, cpu_quota)

        gpu_consumed = _parse_int(item.get("total_consumed_gpu_minutes"))
        gpu_preallocated = _parse_int(item.get("total_preallocated_gpu_minutes"))
        gpu_quota = _parse_int(item.get("quota_gpu_minutes"), -1)
        gpu_percent = _usage_percent(gpu_consumed, gpu_preallocated, gpu_quota)

        decorated.append(
            {
                "name": str(item.get(name_key, "?")),
                "job_count": _parse_int(item.get("job_count")),
                "last_updated": item.get("last_updated") or "n/a",
                "cpu": {
                    "consumed": _format_minutes(cpu_consumed, display_hours),
                    "preallocated": _format_minutes(cpu_preallocated, display_hours),
                    "quota": _quota_label(cpu_quota, display_hours),
                    "percent": cpu_percent,
                    "status_class": _status_class(cpu_percent),
                },
                "gpu": {
                    "consumed": _format_minutes(gpu_consumed, display_hours),
                    "preallocated": _format_minutes(gpu_preallocated, display_hours),
                    "quota": _quota_label(gpu_quota, display_hours),
                    "percent": gpu_percent,
                    "status_class": _status_class(gpu_percent),
                },
            }
        )
    return decorated


@app.get("/")
def dashboard() -> str:
    username = (request.args.get("username") or "").strip() or None
    account = (request.args.get("account") or "").strip() or None
    unit = (request.args.get("unit") or "").strip().lower()
    if unit in ("hours", "h"):
        display_hours = True
    elif unit in ("minutes", "m", "min"):
        display_hours = False
    elif (request.args.get("hours") or "").lower() in {"1", "true", "yes", "on"}:
        display_hours = True
    else:
        display_hours = False

    error: Optional[str] = None
    users: List[Dict[str, Any]] = []
    accounts: List[Dict[str, Any]] = []

    if username and account:
        error = "username and account filters are mutually exclusive."
    else:
        try:
            users_raw, accounts_raw = stats_client.fetch_stats(
                username, account, show_all=username is None
            )
            users = _decorate_rows(users_raw, "username", display_hours)
            accounts = _decorate_rows(accounts_raw, "account", display_hours)
        except (URLError, ServiceHTTPError) as exc:
            error = f"Failed to retrieve stats from service: {exc}"

    return render_template(
        "dashboard.html",
        error=error,
        users=users,
        accounts=accounts,
        selected_username=username or "",
        selected_account=account or "",
        display_hours=display_hours,
        unit_label="hours" if display_hours else "minutes",
    )


def main() -> None:
    host = os.environ.get("SLURM_QUOTA_WEB_HOST", "127.0.0.1")
    port = _parse_int(os.environ.get("SLURM_QUOTA_WEB_PORT"), 5000)
    debug = os.environ.get("SLURM_QUOTA_WEB_DEBUG") == "1"
    app.run(host=host, port=port, debug=debug)
