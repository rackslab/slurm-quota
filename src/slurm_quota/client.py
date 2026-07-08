# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""HTTP client for the slurm-quota stats API."""

from __future__ import annotations

import json
import logging
import os
import ssl
from http import HTTPStatus
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

logger = logging.getLogger("slurm_quota")


class ServiceHTTPError(Exception):
    """Raised when an HTTP service endpoint returns a non-success status."""

    def __init__(
        self,
        status: int,
        *,
        message: str | None = None,
        error: str | None = None,
    ):
        self.status = status
        self.message = message
        self.error = error
        super().__init__(f"HTTP {status}")

    @classmethod
    def from_http_error(cls, exc: HTTPError) -> ServiceHTTPError:
        payload: dict[str, Any] = {}
        try:
            raw = exc.read()
            if raw:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    payload = parsed
        except (json.JSONDecodeError, OSError):
            pass
        message = payload.get("message")
        error = payload.get("error")
        return cls(
            exc.code,
            message=message if isinstance(message, str) else None,
            error=error if isinstance(error, str) else None,
        )


class ServiceUnreachableError(Exception):
    """Raised when the slurm-quota service cannot be reached."""

    @classmethod
    def from_url_error(cls, exc: URLError) -> ServiceUnreachableError:
        if exc.reason is not None:
            return cls(str(exc.reason))
        return cls(str(exc))


def _default_base_url() -> str:
    return os.environ.get("SLURM_QUOTA_URL", "http://127.0.0.1:9911/")


def _ssl_context(base_url: str) -> ssl.SSLContext | None:
    ca_cert = os.environ.get("SLURM_QUOTA_CA_CERT")
    if ca_cert:
        return ssl.create_default_context(cafile=ca_cert)
    if urlparse(base_url).scheme == "https":
        return ssl.create_default_context()
    return None


class APIClient:
    """HTTP client for the slurm-quota REST API."""

    def __init__(self, token: str | None = None, base_url: str | None = None):
        self.token = token
        self.base_url = base_url if base_url is not None else _default_base_url()

    def _request_headers(self, *, include_auth: bool = False) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if include_auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _api_request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        require_token: bool = True,
        include_auth: bool | None = None,
        success_status: int = HTTPStatus.OK,
    ) -> dict[str, Any]:
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
        try:
            with urlopen(request, context=_ssl_context(self.base_url)) as resp:
                status = resp.status
                raw = resp.read()
                payload: dict[str, Any] = {}
                if raw:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        payload = parsed
                if status != success_status:
                    raise ServiceHTTPError(status)
                return payload
        except HTTPError as exc:
            raise ServiceHTTPError.from_http_error(exc) from exc
        except URLError as exc:
            raise ServiceUnreachableError.from_url_error(exc) from exc

    def login(self, username: str, password: str) -> dict[str, Any]:
        """
        Request a JWT from POST /login and store it on this client.

        Args:
            username: LDAP username.
            password: LDAP password.

        Returns:
            Full JSON payload from the login response.

        Raises:
            ServiceHTTPError: Response status was not HTTP OK.
            ServiceUnreachableError: Network or URL handling failure from urlopen.
            ValueError: Response body did not contain a token.
        """
        payload = self._api_request(
            "POST",
            "login",
            body={"username": username, "password": password},
            require_token=False,
            include_auth=False,
        )

        token = payload.get("token")
        if not isinstance(token, str) or not token:
            raise ValueError("login response did not contain a token")
        self.token = token
        return payload

    def stats(
        self,
        selected_username: str | None,
        selected_account: str | None,
        show_all: bool,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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
            ServiceUnreachableError: Network or URL handling failure from urlopen.
        """
        stats_params: dict[str, str] = {}
        if selected_username and not show_all:
            stats_params["username"] = selected_username
        if selected_account:
            stats_params["account"] = selected_account
        stats_payload = self._api_request(
            "GET",
            "stats",
            params=stats_params or None,
            include_auth=True,
        )

        users_data: list[dict[str, Any]] = list(stats_payload.get("users", []))
        accounts_data: list[dict[str, Any]] = list(stats_payload.get("accounts", []))
        return users_data, accounts_data

    def me(self) -> dict[str, Any]:
        return self._api_request("GET", "me")

    def users_roles(self) -> list[dict[str, Any]]:
        payload = self._api_request("GET", "roles")
        users = payload.get("users", [])
        if not isinstance(users, list):
            return []
        return list(users)

    def grant_role(self, role: Literal["operator", "manager"], username: str) -> None:
        self._api_request(
            "PUT",
            f"roles/{role}s/{username}",
            require_token=True,
            success_status=HTTPStatus.NO_CONTENT,
        )

    def revoke_role(self, role: Literal["operator", "manager"], username: str) -> None:
        self._api_request(
            "DELETE",
            f"roles/{role}s/{username}",
            require_token=True,
            success_status=HTTPStatus.NO_CONTENT,
        )

    def list_manager_accounts(self, username: str) -> list[str]:
        payload = self._api_request(
            "GET", f"roles/managers/{username}/accounts", require_token=True
        )
        accounts = payload.get("accounts", [])
        if not isinstance(accounts, list):
            return []
        return [str(account) for account in accounts]

    def add_manager_account(self, username: str, account: str) -> None:
        self._api_request(
            "PUT",
            f"roles/managers/{username}/accounts/{account}",
            require_token=True,
            success_status=HTTPStatus.NO_CONTENT,
        )

    def remove_manager_account(self, username: str, account: str) -> None:
        self._api_request(
            "DELETE",
            f"roles/managers/{username}/accounts/{account}",
            require_token=True,
            success_status=HTTPStatus.NO_CONTENT,
        )

    def set_user_cpu_quota(self, username: str, quota_minutes: int) -> None:
        self._api_request(
            "PUT",
            f"quotas/users/{username}/cpu",
            body={"quota_minutes": quota_minutes},
            require_token=True,
            success_status=HTTPStatus.NO_CONTENT,
        )

    def set_user_gpu_quota(self, username: str, quota_minutes: int) -> None:
        self._api_request(
            "PUT",
            f"quotas/users/{username}/gpu",
            body={"quota_minutes": quota_minutes},
            require_token=True,
            success_status=HTTPStatus.NO_CONTENT,
        )

    def set_account_cpu_quota(self, account: str, quota_minutes: int) -> None:
        self._api_request(
            "PUT",
            f"quotas/accounts/{account}/cpu",
            body={"quota_minutes": quota_minutes},
            require_token=True,
            success_status=HTTPStatus.NO_CONTENT,
        )

    def set_account_gpu_quota(self, account: str, quota_minutes: int) -> None:
        self._api_request(
            "PUT",
            f"quotas/accounts/{account}/gpu",
            body={"quota_minutes": quota_minutes},
            require_token=True,
            success_status=HTTPStatus.NO_CONTENT,
        )

    def get_default_quotas(self) -> dict[str, int]:
        payload = self._api_request("GET", "quotas/defaults", require_token=True)
        result: dict[str, int] = {}
        for field in (
            "user_cpu_minutes",
            "user_gpu_minutes",
            "account_cpu_minutes",
            "account_gpu_minutes",
        ):
            value = payload.get(field)
            if not isinstance(value, int):
                raise ValueError(f"default quotas response missing {field}")
            result[field] = value
        return result

    def set_default_quotas(
        self,
        *,
        user_cpu: int | None = None,
        user_gpu: int | None = None,
        account_cpu: int | None = None,
        account_gpu: int | None = None,
    ) -> None:
        body: dict[str, int] = {}
        if user_cpu is not None:
            body["user_cpu_minutes"] = user_cpu
        if user_gpu is not None:
            body["user_gpu_minutes"] = user_gpu
        if account_cpu is not None:
            body["account_cpu_minutes"] = account_cpu
        if account_gpu is not None:
            body["account_gpu_minutes"] = account_gpu
        self._api_request(
            "PUT",
            "quotas/defaults",
            body=body,
            require_token=True,
            success_status=HTTPStatus.NO_CONTENT,
        )

    def adjust_consumption(
        self,
        target: Literal["user", "account"],
        name: str,
        resource: Literal["cpu", "gpu"],
        delta_minutes: int,
    ) -> int:
        path = f"consumption/{target}/{name}/{resource}"
        payload = self._api_request(
            "PATCH",
            path,
            body={"delta_minutes": delta_minutes},
            require_token=True,
        )
        total = payload.get("total_consumed_minutes")
        if not isinstance(total, int):
            raise ValueError("adjust response did not contain total_consumed_minutes")
        return total

    def get_gpu_factors(self) -> dict[str, Any]:
        payload = self._api_request("GET", "factors/gpu", require_token=True)
        default_factor = payload.get("default_factor")
        factors = payload.get("factors")
        if not isinstance(default_factor, (int, float)):
            raise ValueError("gpu factors response missing default_factor")
        if not isinstance(factors, dict):
            raise ValueError("gpu factors response missing factors")
        return {
            "default_factor": float(default_factor),
            "factors": {str(k): float(v) for k, v in factors.items()},
        }

    def set_gpu_factor(self, gpu_type: str, factor: float) -> None:
        self._api_request(
            "PUT",
            f"factors/gpu/{gpu_type}",
            body={"factor": factor},
            require_token=True,
            success_status=HTTPStatus.NO_CONTENT,
        )
