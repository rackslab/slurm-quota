# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""HTTP route handlers for the slurm-quota web dashboard."""

from __future__ import annotations

import logging
import secrets
from typing import TYPE_CHECKING, Any, Dict, List, Optional, cast
from urllib.error import URLError

from flask import (
    Response,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from slurm_quota.client import APIClient, ServiceHTTPError
from slurm_quota.web import dashboard as dashboard_view
from slurm_quota.web.auth import (
    api_client,
    csrf_token,
    login_url,
    session_token,
    validate_csrf,
)

if TYPE_CHECKING:
    from slurm_quota.web.app import SlurmQuotaWebApp

logger = logging.getLogger("slurm_quota")


def _web_app() -> SlurmQuotaWebApp:
    return cast("SlurmQuotaWebApp", current_app)


def login() -> Response | str:
    try:
        auth_required = _web_app().auth_required_cached()
    except (URLError, ServiceHTTPError) as exc:
        logger.error("Failed to contact service: %s", exc)
        abort(503, description=f"Failed to contact service: {exc}")

    if not auth_required:
        return redirect(url_for("dashboard"))

    if session_token() is not None:
        return redirect(request.args.get("next") or url_for("dashboard"))

    _web_app().ensure_session_key()
    message_key = request.args.get("message")
    info_message = None
    if message_key == "session_expired":
        info_message = "Your session has expired. Please sign in again."
    return render_template(
        "login.html",
        error=None,
        info_message=info_message,
        csrf_token=csrf_token(),
        next_url=request.args.get("next") or url_for("dashboard"),
        auth_required=True,
    )


def login_post() -> Any:
    try:
        if not _web_app().auth_required_cached():
            return redirect(url_for("dashboard"))
    except (URLError, ServiceHTTPError) as exc:
        logger.error("Failed to contact service: %s", exc)
        abort(503, description=f"Failed to contact service: {exc}")

    _web_app().ensure_session_key()
    next_url = request.form.get("next") or url_for("dashboard")

    if not validate_csrf():
        return (
            render_template(
                "login.html",
                error="Invalid or missing CSRF token.",
                csrf_token=csrf_token(),
                next_url=next_url,
                auth_required=True,
            ),
            400,
        )

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    if not username or not password:
        return render_template(
            "login.html",
            error="Username and password are required.",
            csrf_token=csrf_token(),
            next_url=next_url,
            auth_required=True,
        )

    try:
        api = APIClient()
        api.login(username, password)
        token = api.token
    except ServiceHTTPError as exc:
        if exc.status == 401:
            return render_template(
                "login.html",
                error="Invalid username or password.",
                csrf_token=csrf_token(),
                next_url=next_url,
                auth_required=True,
            )
        return render_template(
            "login.html",
            error=f"Login failed: HTTP {exc.status}",
            csrf_token=csrf_token(),
            next_url=next_url,
            auth_required=True,
        )
    except (URLError, ValueError) as exc:
        return render_template(
            "login.html",
            error=f"Login failed: {exc}",
            csrf_token=csrf_token(),
            next_url=next_url,
            auth_required=True,
        )

    session.clear()
    session.permanent = True
    session["token"] = token
    session["username"] = username
    session["_csrf"] = secrets.token_hex(32)
    return redirect(next_url)


def logout() -> Any:
    if not validate_csrf():
        abort(400, description="Invalid or missing CSRF token.")
    session.clear()
    try:
        if _web_app().auth_required_cached(refresh=True):
            return redirect(url_for("login"))
    except (URLError, ServiceHTTPError):
        pass
    return redirect(url_for("dashboard"))


def dashboard() -> Response | str:
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
    auth_required = False
    logged_in_username: Optional[str] = None

    try:
        auth_required = _web_app().auth_required_cached()
    except (URLError, ServiceHTTPError):
        auth_required = False

    if auth_required:
        logged_in_username = session.get("username")
        if isinstance(logged_in_username, str):
            logged_in_username = logged_in_username or None
        else:
            logged_in_username = None

    if username and account:
        error = "username and account filters are mutually exclusive."
    else:
        try:
            users_raw, accounts_raw = api_client().stats(
                username,
                account,
                show_all=username is None,
            )
            users = dashboard_view.decorate_rows(users_raw, "username", display_hours)
            accounts = dashboard_view.decorate_rows(
                accounts_raw, "account", display_hours
            )
        except ServiceHTTPError as exc:
            if exc.status in (401, 403) and auth_required:
                session.clear()
                _web_app().invalidate_auth_required_cache()
                return redirect(login_url(message="session_expired"))
            error = f"Failed to retrieve stats from service: {exc}"
        except URLError as exc:
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
        auth_required=auth_required,
        logged_in_username=logged_in_username,
        csrf_token=csrf_token() if auth_required else None,
    )
