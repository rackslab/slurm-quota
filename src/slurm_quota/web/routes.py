# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""HTTP route handlers for the slurm-quota web dashboard."""

from __future__ import annotations

import logging
import secrets
from typing import TYPE_CHECKING, Any, Literal, cast

from flask import (
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from slurm_quota.client import APIClient, ServiceHTTPError, ServiceUnreachableError
from slurm_quota.web import dashboard as dashboard_view
from slurm_quota.web.auth import (
    api_client,
    csrf_token,
    current_role,
    login_url,
    resolve_next_url,
    session_token,
    validate_csrf,
)

if TYPE_CHECKING:
    from slurm_quota.web.app import SlurmQuotaWebApp

logger = logging.getLogger("slurm_quota")


def _web_app() -> SlurmQuotaWebApp:
    return cast("SlurmQuotaWebApp", current_app)


def _display_hours_from_unit(unit: str, hours_legacy: str) -> bool:
    """Return whether stats should be shown in hours from query parameters.

    The unit argument accepts hours/h or minutes/m/min. When unit is absent,
    the legacy hours flag (1, true, etc.) is consulted. Defaults to minutes.
    """
    normalized = unit.strip().lower()
    if normalized in ("hours", "h"):
        return True
    if normalized in ("minutes", "m", "min"):
        return False
    return hours_legacy.lower() in {"1", "true", "yes", "on"}


def _resolve_dashboard_filters() -> tuple[str | None, str | None, bool]:
    """Resolve dashboard filter state using query parameters and session.

    When the request includes any of username, account, unit, or hours query
    parameters, those values are used and persisted to session dashboard_filters
    so POST redirects and bare GET / requests keep the same view. Otherwise the
    last saved session filters are restored. With neither query parameters nor a
    saved session, all filters are cleared and minutes are used.

    Returns (username, account, display_hours). username and account are None
    when unset; they are mutually exclusive when both are set by the user
    (handled by the caller).
    """
    if any(key in request.args for key in ("username", "account", "unit", "hours")):
        username = (request.args.get("username") or "").strip() or None
        account = (request.args.get("account") or "").strip() or None
        display_hours = _display_hours_from_unit(
            request.args.get("unit") or "",
            request.args.get("hours") or "",
        )
        session["dashboard_filters"] = {
            "username": username or "",
            "account": account or "",
            "unit": "hours" if display_hours else "minutes",
        }
        return username, account, display_hours

    stored = session.get("dashboard_filters")
    if isinstance(stored, dict):
        username = (stored.get("username") or "").strip() or None
        account = (stored.get("account") or "").strip() or None
        display_hours = stored.get("unit") == "hours"
        return username, account, display_hours

    return None, None, False


def login() -> Response | str:
    if session_token() is not None:
        return redirect(resolve_next_url(request.args.get("next")))

    _web_app().ensure_session_key()
    message_key = request.args.get("message")
    info_message = None
    if message_key == "session_expired":
        info_message = "Your session has expired. Please sign in again."
    return render_template(
        "login.html",
        info_message=info_message,
        csrf_token=csrf_token(),
        next_url=resolve_next_url(request.args.get("next")),
        auth_required=True,
    )


def login_post() -> Any:
    _web_app().ensure_session_key()
    next_url = resolve_next_url(request.form.get("next"))

    if not validate_csrf():
        flash("Invalid or missing CSRF token.", "error")
        return (
            render_template(
                "login.html",
                csrf_token=csrf_token(),
                next_url=next_url,
                auth_required=True,
            ),
            400,
        )

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    if not username or not password:
        flash("Username and password are required.", "error")
        return render_template(
            "login.html",
            csrf_token=csrf_token(),
            next_url=next_url,
            auth_required=True,
        )

    try:
        api = APIClient()
        payload = api.login(username, password)
        token = api.token
        role = payload["role"]
    except ServiceHTTPError as exc:
        if exc.status == 401:
            flash("Invalid username or password.", "error")
            return render_template(
                "login.html",
                csrf_token=csrf_token(),
                next_url=next_url,
                auth_required=True,
            )
        flash(f"Login failed: HTTP {exc.status}", "error")
        return render_template(
            "login.html",
            csrf_token=csrf_token(),
            next_url=next_url,
            auth_required=True,
        )
    except (ServiceUnreachableError, ValueError) as exc:
        flash(f"Login failed: {exc}", "error")
        return render_template(
            "login.html",
            csrf_token=csrf_token(),
            next_url=next_url,
            auth_required=True,
        )

    session.clear()
    session.permanent = True
    session["token"] = token
    session["username"] = username
    session["role"] = role
    session["_csrf"] = secrets.token_hex(32)
    return redirect(next_url)


def logout() -> Any:
    if not validate_csrf():
        abort(400, description="Invalid or missing CSRF token.")
    session.clear()
    return redirect(url_for("login"))


def dashboard() -> Response | str:
    role = current_role()
    username, account, display_hours = _resolve_dashboard_filters()

    users: list[dict[str, Any]] = []
    accounts: list[dict[str, Any]] = []

    logged_in_username = session.get("username")
    if isinstance(logged_in_username, str):
        logged_in_username = logged_in_username or None
    else:
        logged_in_username = None

    if username and account:
        flash("username and account filters are mutually exclusive.", "error")
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
            if exc.status == 401:
                session.clear()
                return redirect(login_url(message="session_expired"))
            flash(f"Failed to retrieve stats from service: {exc}", "error")
        except ServiceUnreachableError as exc:
            flash(f"Failed to retrieve stats from service: {exc}", "error")

    return render_template(
        "dashboard.html",
        users=users,
        accounts=accounts,
        selected_username=username or "",
        selected_account=account or "",
        display_hours=display_hours,
        unit_label="hours" if display_hours else "minutes",
        auth_required=True,
        logged_in_username=logged_in_username,
        user_role=role,
        csrf_token=csrf_token(),
    )


def roles() -> Response | str:
    role = current_role()
    if role != "admin":
        return redirect(url_for("dashboard"))

    users: list[dict[str, Any]] = []
    try:
        users = api_client().users_roles()
    except ServiceHTTPError as exc:
        if exc.status in (401, 403):
            session.clear()
            return redirect(login_url(message="session_expired"))
        flash(f"Failed to retrieve roles from service: HTTP {exc.status}", "error")
    except ServiceUnreachableError as exc:
        flash(f"Failed to retrieve roles from service: {exc}", "error")

    logged_in_username = session.get("username")
    if isinstance(logged_in_username, str):
        logged_in_username = logged_in_username or None
    else:
        logged_in_username = None

    return render_template(
        "roles.html",
        users=users,
        csrf_token=csrf_token(),
        auth_required=True,
        logged_in_username=logged_in_username,
    )


def roles_post() -> Any:
    role = current_role()
    if role != "admin":
        return redirect(url_for("dashboard"))

    if not validate_csrf():
        abort(400, description="Invalid or missing CSRF token.")

    action = (request.form.get("action") or "").strip()
    target_role = (request.form.get("role") or "").strip()
    username = (request.form.get("username") or "").strip()
    if not username:
        return redirect(url_for("roles"))

    try:
        api = api_client()
        if action == "grant" and target_role in ("operator", "manager"):
            api.grant_role(cast(Literal["operator", "manager"], target_role), username)
            flash(f"Role updated for {username}.", "success")
        elif action == "revoke" and target_role in ("operator", "manager"):
            api.revoke_role(cast(Literal["operator", "manager"], target_role), username)
            flash(f"Role updated for {username}.", "success")
        else:
            flash("Unknown or invalid role action.", "error")
    except ServiceHTTPError as exc:
        if exc.status in (401, 403):
            session.clear()
            return redirect(login_url(message="session_expired"))
        flash(f"Failed to update role: HTTP {exc.status}", "error")
    except ServiceUnreachableError as exc:
        flash(f"Failed to update role: {exc}", "error")

    return redirect(url_for("roles"))


def roles_manager_accounts_post() -> Any:
    role = current_role()
    if role != "admin":
        return redirect(url_for("dashboard"))

    if not validate_csrf():
        abort(400, description="Invalid or missing CSRF token.")

    action = (request.form.get("action") or "").strip()
    username = (request.form.get("username") or "").strip()
    account = (request.form.get("account") or "").strip()
    if not username or not account:
        return redirect(url_for("roles"))

    try:
        api = api_client()
        if action == "add":
            api.add_manager_account(username, account)
            flash("Manager account updated.", "success")
        elif action == "remove":
            api.remove_manager_account(username, account)
            flash("Manager account updated.", "success")
        else:
            flash("Unknown or invalid manager account action.", "error")
    except ServiceHTTPError as exc:
        if exc.status in (401, 403):
            session.clear()
            return redirect(login_url(message="session_expired"))
        flash(f"Failed to update manager account: HTTP {exc.status}", "error")
    except ServiceUnreachableError as exc:
        flash(f"Failed to update manager account: {exc}", "error")

    return redirect(url_for("roles"))


def _optional_form_reason() -> str | None:
    reason = (request.form.get("reason") or "").strip()
    return reason or None


def quotas_post() -> Any:
    role = current_role()
    if role not in ("admin", "operator"):
        return redirect(url_for("dashboard"))

    if not validate_csrf():
        abort(400, description="Invalid or missing CSRF token.")

    target = (request.form.get("target") or "").strip()
    name = (request.form.get("name") or "").strip()
    resource = (request.form.get("resource") or "").strip()
    unlimited = request.form.get("unlimited") == "on"
    quota_raw = (request.form.get("quota_minutes") or "").strip()

    if not name or target not in ("user", "account") or resource not in ("cpu", "gpu"):
        flash("Invalid quota target or resource.", "error")
        return redirect(url_for("dashboard"))

    if unlimited:
        quota_minutes = -1
    else:
        try:
            quota_minutes = int(quota_raw)
        except ValueError:
            flash("Invalid quota value.", "error")
            return redirect(url_for("dashboard"))
        if quota_minutes < -1:
            flash("Quota value must be -1 or greater.", "error")
            return redirect(url_for("dashboard"))

    try:
        api = api_client()
        reason = _optional_form_reason()
        if target == "user" and resource == "cpu":
            api.set_user_cpu_quota(name, quota_minutes, reason=reason)
        elif target == "user" and resource == "gpu":
            api.set_user_gpu_quota(name, quota_minutes, reason=reason)
        elif target == "account" and resource == "cpu":
            api.set_account_cpu_quota(name, quota_minutes, reason=reason)
        elif target == "account" and resource == "gpu":
            api.set_account_gpu_quota(name, quota_minutes, reason=reason)
        flash(f"Quota updated for {name}.", "success")
    except ServiceHTTPError as exc:
        if exc.status in (401, 403):
            session.clear()
            return redirect(login_url(message="session_expired"))
        flash(f"Failed to update quota: HTTP {exc.status}", "error")
    except ServiceUnreachableError as exc:
        flash(f"Failed to update quota: {exc}", "error")

    return redirect(url_for("dashboard"))


def consumption_post() -> Any:
    role = current_role()
    if role not in ("admin", "operator"):
        return redirect(url_for("dashboard"))

    if not validate_csrf():
        abort(400, description="Invalid or missing CSRF token.")

    target = (request.form.get("target") or "").strip()
    name = (request.form.get("name") or "").strip()
    resource = (request.form.get("resource") or "").strip()
    delta_raw = (request.form.get("delta_minutes") or "").strip()

    if not name or target not in ("user", "account") or resource not in ("cpu", "gpu"):
        flash("Invalid consumption target or resource.", "error")
        return redirect(url_for("dashboard"))

    try:
        delta_minutes = int(delta_raw)
    except ValueError:
        flash("Invalid consumption adjustment value.", "error")
        return redirect(url_for("dashboard"))

    try:
        api = api_client()
        reason = _optional_form_reason()
        api.adjust_consumption(target, name, resource, delta_minutes, reason=reason)
        flash(f"Consumption adjusted for {name}.", "success")
    except ServiceHTTPError as exc:
        if exc.status in (401, 403):
            session.clear()
            return redirect(login_url(message="session_expired"))
        flash(f"Failed to adjust consumption: HTTP {exc.status}", "error")
    except ServiceUnreachableError as exc:
        flash(f"Failed to adjust consumption: {exc}", "error")

    return redirect(url_for("dashboard"))
