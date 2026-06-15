# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""HTTP client for the slurm-quota stats API."""

import json

import os
from http import HTTPStatus
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

import logging

logger = logging.getLogger("slurm_quota")


class StatsHTTPError(Exception):
    """Raised when the stats HTTP endpoint returns a non-success status."""

    def __init__(self, status: int):
        self.status = status
        super().__init__(f"HTTP {status}")


def fetch_stats_from_service(
    selected_username: Optional[str], selected_account: Optional[str], show_all: bool
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Request /stats from the slurm-quota HTTP service and return user and account rows.

    Uses environment variable SLURM_QUOTA_URL as the service root, defaulting to
    http://127.0.0.1:9911/.

    Args:
        selected_username: User filter when not show_all (may be None if show_all).
        selected_account: Account filter to apply on account stats.
        show_all: If True, do not add a username query parameter.

    Returns:
        (users_data, accounts_data) lists from the JSON payload.

    Raises:
        StatsHTTPError: Response status was not HTTP OK.
        URLError: Network or URL handling failure from urlopen.
    """
    base_url = os.environ.get("SLURM_QUOTA_URL", "http://127.0.0.1:9911/")
    stats_params: Dict[str, str] = {}
    if selected_username and not show_all:
        stats_params["username"] = selected_username
    if selected_account:
        stats_params["account"] = selected_account
    stats_url = urljoin(base_url, "stats")
    if stats_params:
        stats_url = f"{stats_url}?{urlencode(stats_params)}"

    with urlopen(Request(stats_url, headers={"Accept": "application/json"})) as resp:
        if resp.status != HTTPStatus.OK:
            raise StatsHTTPError(resp.status)
        stats_payload: Dict[str, Any] = json.load(resp)

    users_data: List[Dict[str, Any]] = list(stats_payload.get("users", []))
    accounts_data: List[Dict[str, Any]] = list(stats_payload.get("accounts", []))
    return users_data, accounts_data
