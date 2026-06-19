# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Session and API authentication helpers for the web dashboard."""

from __future__ import annotations

import secrets
from typing import Optional
from urllib.parse import quote

from flask import request, session, url_for

from slurm_quota.client import APIClient
from slurm_quota.token import load_service_token


def session_token() -> Optional[str]:
    token = session.get("token")
    if isinstance(token, str) and token:
        return token
    return None


def api_client() -> APIClient:
    return APIClient(token=session_token() or load_service_token())


def csrf_token() -> str:
    token = session.get("_csrf")
    if not isinstance(token, str) or not token:
        token = secrets.token_hex(32)
        session["_csrf"] = token
    return token


def validate_csrf() -> bool:
    submitted = request.form.get("_csrf")
    expected = session.get("_csrf")
    if not isinstance(submitted, str) or not isinstance(expected, str):
        return False
    return secrets.compare_digest(submitted, expected)


def login_url(*, message: Optional[str] = None) -> str:
    next_url = request.full_path if request.method == "GET" else request.path
    if next_url in ("/login", "/login?"):
        next_url = "/"
    query = f"next={quote(next_url, safe='')}"
    if message:
        query = f"{query}&message={quote(message, safe='')}"
    return url_for("login", _external=False) + "?" + query
