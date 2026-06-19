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


class ServiceHTTPError(Exception):
    """Raised when an HTTP service endpoint returns a non-success status."""

    def __init__(self, status: int):
        self.status = status
        super().__init__(f"HTTP {status}")


def _default_base_url() -> str:
    return os.environ.get("SLURM_QUOTA_URL", "http://127.0.0.1:9911/")


class APIClient:
    """HTTP client for the slurm-quota REST API."""

    def __init__(self, token: Optional[str] = None, base_url: Optional[str] = None):
        self.token = token
        self.base_url = base_url if base_url is not None else _default_base_url()

    def _request_headers(self, *, include_auth: bool = False) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if include_auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def login(self, username: str, password: str) -> str:
        """
        Request a JWT from POST /login and store it on this client.

        Args:
            username: LDAP username.
            password: LDAP password.

        Returns:
            JWT token string from the JSON response.

        Raises:
            ServiceHTTPError: Response status was not HTTP OK.
            URLError: Network or URL handling failure from urlopen.
            ValueError: Response body did not contain a token.
        """
        login_url = urljoin(self.base_url, "login")
        body = json.dumps({"username": username, "password": password}).encode("utf-8")
        request = Request(
            login_url,
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request) as resp:
            if resp.status != HTTPStatus.OK:
                raise ServiceHTTPError(resp.status)
            payload: Dict[str, Any] = json.load(resp)

        token = payload.get("token")
        if not isinstance(token, str) or not token:
            raise ValueError("login response did not contain a token")
        self.token = token
        return token

    def stats(
        self,
        selected_username: Optional[str],
        selected_account: Optional[str],
        show_all: bool,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Request /stats and return user and account rows.

        Sends self.token as a Bearer header when set.

        Args:
            selected_username: User filter when not show_all (may be None if show_all).
            selected_account: Account filter to apply on account stats.
            show_all: If True, do not add a username query parameter.

        Returns:
            (users_data, accounts_data) lists from the JSON payload.

        Raises:
            ServiceHTTPError: Response status was not HTTP OK.
            URLError: Network or URL handling failure from urlopen.
        """
        stats_params: Dict[str, str] = {}
        if selected_username and not show_all:
            stats_params["username"] = selected_username
        if selected_account:
            stats_params["account"] = selected_account
        stats_url = urljoin(self.base_url, "stats")
        if stats_params:
            stats_url = f"{stats_url}?{urlencode(stats_params)}"

        with urlopen(
            Request(stats_url, headers=self._request_headers(include_auth=True))
        ) as resp:
            if resp.status != HTTPStatus.OK:
                raise ServiceHTTPError(resp.status)
            stats_payload: Dict[str, Any] = json.load(resp)

        users_data: List[Dict[str, Any]] = list(stats_payload.get("users", []))
        accounts_data: List[Dict[str, Any]] = list(stats_payload.get("accounts", []))
        return users_data, accounts_data
