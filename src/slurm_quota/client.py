# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""HTTP client for the slurm-quota stats API."""

import json

import os
from http import HTTPStatus
from typing import Any, Dict, List, Literal, Optional, Tuple
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

    def _api_request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, str]] = None,
        require_token: bool = True,
        include_auth: Optional[bool] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        if require_token and not self.token:
            raise ValueError("API token is required")
        if include_auth is None:
            include_auth = require_token
        url = urljoin(self.base_url, path)
        if params:
            url = f"{url}?{urlencode(params)}"
        data = None
        headers = self._request_headers(include_auth=include_auth)
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method)
        with urlopen(request) as resp:
            status = resp.status
            raw = resp.read()
            if not raw:
                return status, {}
            payload: Dict[str, Any] = json.loads(raw)
            return status, payload

    def login(self, username: str, password: str) -> Dict[str, Any]:
        """
        Request a JWT from POST /login and store it on this client.

        Args:
            username: LDAP username.
            password: LDAP password.

        Returns:
            Full JSON payload from the login response.

        Raises:
            ServiceHTTPError: Response status was not HTTP OK.
            URLError: Network or URL handling failure from urlopen.
            ValueError: Response body did not contain a token.
        """
        status, payload = self._api_request(
            "POST",
            "login",
            body={"username": username, "password": password},
            require_token=False,
            include_auth=False,
        )
        if status != HTTPStatus.OK:
            raise ServiceHTTPError(status)

        token = payload.get("token")
        if not isinstance(token, str) or not token:
            raise ValueError("login response did not contain a token")
        self.token = token
        return payload

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
        status, stats_payload = self._api_request(
            "GET",
            "stats",
            params=stats_params or None,
            require_token=False,
            include_auth=True,
        )
        if status != HTTPStatus.OK:
            raise ServiceHTTPError(status)

        users_data: List[Dict[str, Any]] = list(stats_payload.get("users", []))
        accounts_data: List[Dict[str, Any]] = list(stats_payload.get("accounts", []))
        return users_data, accounts_data

    def me(self) -> Dict[str, Any]:
        status, payload = self._api_request("GET", "me")
        if status != HTTPStatus.OK:
            raise ServiceHTTPError(status)
        return payload

    def users_roles(self) -> List[Dict[str, Any]]:
        status, payload = self._api_request("GET", "roles")
        if status != HTTPStatus.OK:
            raise ServiceHTTPError(status)
        users = payload.get("users", [])
        if not isinstance(users, list):
            return []
        return list(users)

    def grant_manager(self, username: str) -> None:
        status, _payload = self._api_request(
            "PUT", f"roles/managers/{username}", require_token=True
        )
        if status != HTTPStatus.NO_CONTENT:
            raise ServiceHTTPError(status)

    def revoke_manager(self, username: str) -> None:
        status, _payload = self._api_request(
            "DELETE", f"roles/managers/{username}", require_token=True
        )
        if status != HTTPStatus.NO_CONTENT:
            raise ServiceHTTPError(status)

    def set_user_cpu_quota(self, username: str, quota_minutes: int) -> None:
        status, _payload = self._api_request(
            "PUT",
            f"quotas/users/{username}/cpu",
            body={"quota_minutes": quota_minutes},
            require_token=True,
        )
        if status != HTTPStatus.NO_CONTENT:
            raise ServiceHTTPError(status)

    def set_user_gpu_quota(self, username: str, quota_minutes: int) -> None:
        status, _payload = self._api_request(
            "PUT",
            f"quotas/users/{username}/gpu",
            body={"quota_minutes": quota_minutes},
            require_token=True,
        )
        if status != HTTPStatus.NO_CONTENT:
            raise ServiceHTTPError(status)

    def set_account_cpu_quota(self, account: str, quota_minutes: int) -> None:
        status, _payload = self._api_request(
            "PUT",
            f"quotas/accounts/{account}/cpu",
            body={"quota_minutes": quota_minutes},
            require_token=True,
        )
        if status != HTTPStatus.NO_CONTENT:
            raise ServiceHTTPError(status)

    def set_account_gpu_quota(self, account: str, quota_minutes: int) -> None:
        status, _payload = self._api_request(
            "PUT",
            f"quotas/accounts/{account}/gpu",
            body={"quota_minutes": quota_minutes},
            require_token=True,
        )
        if status != HTTPStatus.NO_CONTENT:
            raise ServiceHTTPError(status)

    def adjust_consumption(
        self,
        target: Literal["user", "account"],
        name: str,
        resource: Literal["cpu", "gpu"],
        delta_minutes: int,
    ) -> int:
        path = f"consumption/{target}/{name}/{resource}"
        status, payload = self._api_request(
            "PATCH",
            path,
            body={"delta_minutes": delta_minutes},
            require_token=True,
        )
        if status != HTTPStatus.OK:
            raise ServiceHTTPError(status)
        total = payload.get("total_consumed_minutes")
        if not isinstance(total, int):
            raise ValueError("adjust response did not contain total_consumed_minutes")
        return total
