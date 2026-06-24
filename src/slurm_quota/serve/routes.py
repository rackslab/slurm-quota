# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""HTTP API route handlers for slurm-quota-serve."""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple, cast

from flask import abort, current_app, jsonify, request
from rfl.authentication.errors import LDAPAuthenticationError
from rfl.web.tokens import check_jwt

import slurm_quota
from slurm_quota.database import (
    adjust_consumed_minutes as db_adjust_consumed_minutes,
    get_default_quota_settings,
    grant_api_manager,
    list_users_with_roles,
    query_accounts_aggregate,
    query_users_aggregate,
    revoke_api_manager,
    set_account_gpu_quota as db_set_account_gpu_quota,
    set_account_quota as db_set_account_quota,
    set_default_quota_settings,
    set_user_gpu_quota as db_set_user_gpu_quota,
    set_user_quota as db_set_user_quota,
)
from slurm_quota import slurm as slurm_integration
from slurm_quota.serve.authorization import (
    config_admins,
    login_role,
    require_role,
)

if TYPE_CHECKING:
    from slurm_quota.serve.app import SlurmQuotaServeApp

logger = logging.getLogger("slurm_quota")

_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate_username(username: str) -> None:
    if not username or not _USERNAME_PATTERN.fullmatch(username):
        abort(
            400,
            description="Invalid username",
        )


def _validate_account(account: str) -> None:
    if not account or not _USERNAME_PATTERN.fullmatch(account):
        abort(
            400,
            description="Invalid account",
        )


def fetch_stats(
    username_param: Optional[str],
    account_param: Optional[str],
) -> Tuple[Optional[Dict[str, Any]], Optional[Tuple[Any, int]]]:
    if username_param and account_param:
        return None, (
            jsonify(
                {
                    "error": "bad_request",
                    "message": "username and account are mutually exclusive",
                }
            ),
            400,
        )

    try:
        if not os.path.exists(slurm_quota.DB_PATH):
            return {"users": [], "accounts": []}, None

        accounts_filter: Optional[set[str]] = None
        if username_param:
            try:
                accounts_filter = slurm_integration.get_user_accounts(username_param)
            except Exception:
                accounts_filter = set()
        if account_param:
            accounts_filter = {account_param}

        with sqlite3.connect(slurm_quota.DB_PATH) as conn:
            users = query_users_aggregate(conn, username_param or None)
            if account_param:
                users = []
            accounts = query_accounts_aggregate(conn, accounts_filter)
        return {"users": users, "accounts": accounts}, None
    except sqlite3.Error as exc:
        logger.error("/stats query failed: %s", exc)
        return None, (jsonify({"error": "db_error"}), 500)


def health() -> Any:
    return jsonify({"status": "ok"})


def login() -> Any:
    serve_app = cast("SlurmQuotaServeApp", current_app)
    if (
        serve_app.settings is None
        or serve_app.settings.authentication.method != "ldap"
        or serve_app.authentifier is None
    ):
        return jsonify({"error": "not_found"}), 404

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "bad_request", "message": "Invalid JSON body"}), 400

    username = payload.get("username")
    password = payload.get("password")
    if not isinstance(username, str) or not isinstance(password, str):
        return (
            jsonify(
                {
                    "error": "bad_request",
                    "message": "username and password are required",
                }
            ),
            400,
        )

    try:
        user = serve_app.authentifier.login(username, password)
    except LDAPAuthenticationError:
        abort(401, description="Invalid user or password")

    token = serve_app.jwt.generate(user, serve_app.settings.jwt.duration)
    return jsonify(
        {"token": token, "username": user.login, "role": login_role(user.login)}
    )


@check_jwt
def stats() -> Any:
    username_param = (request.args.get("username") or "").strip() or None
    account_param = (request.args.get("account") or "").strip() or None
    login = cast(str, request.user.login)
    role = login_role(login)
    # If the user has no special role, they can only view their own stats. Check they are not
    # requesting to view stats for other users or accounts they don't belong to.
    if role not in ("admin", "manager"):
        if username_param and username_param != login:
            abort(403, description="Not allowed to view stats for other users")
        if account_param:
            try:
                user_accounts = slurm_integration.get_user_accounts(login)
            except Exception:
                user_accounts = set()
            if account_param not in user_accounts:
                abort(403, description="Not allowed to view stats for this account")
        if username_param is None and account_param is None:
            username_param = login
    payload, error = fetch_stats(username_param, account_param)
    if error is not None:
        response, status = error
        return response, status
    return jsonify(payload)


@check_jwt
def me() -> Any:
    return jsonify(
        {"username": request.user.login, "role": login_role(request.user.login)}
    )


@require_role("admin")
def list_roles() -> Any:
    serve_app = cast("SlurmQuotaServeApp", current_app)
    assert serve_app.settings is not None
    with sqlite3.connect(slurm_quota.DB_PATH) as conn:
        users = list_users_with_roles(conn, config_admins(serve_app.settings))
    return jsonify({"users": users})


@require_role("admin")
def grant_manager(username: str) -> Any:
    _validate_username(username)
    serve_app = cast("SlurmQuotaServeApp", current_app)
    assert serve_app.settings is not None
    if username in config_admins(serve_app.settings):
        return "", 204
    with sqlite3.connect(slurm_quota.DB_PATH) as conn:
        grant_api_manager(conn, username)
    return "", 204


@require_role("admin")
def revoke_manager(username: str) -> Any:
    _validate_username(username)
    with sqlite3.connect(slurm_quota.DB_PATH) as conn:
        revoke_api_manager(conn, username)
    return "", 204


@require_role("admin", "manager")
def get_default_quotas() -> Any:
    try:
        settings = get_default_quota_settings()
    except sqlite3.Error as exc:
        logger.error("get default quotas failed: %s", exc)
        return jsonify({"error": "db_error"}), 500
    return jsonify(
        {
            "user_cpu_minutes": settings["default_user_quota_cpu_minutes"],
            "user_gpu_minutes": settings["default_user_quota_gpu_minutes"],
            "account_cpu_minutes": settings["default_account_quota_cpu_minutes"],
            "account_gpu_minutes": settings["default_account_quota_gpu_minutes"],
        }
    )


@require_role("admin", "manager")
def set_default_quotas() -> Any:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        abort(400, description="Invalid JSON body")

    updates: Dict[str, Optional[int]] = {
        "user_cpu_minutes": None,
        "user_gpu_minutes": None,
        "account_cpu_minutes": None,
        "account_gpu_minutes": None,
    }

    for field in updates:
        if field not in payload:
            continue
        value = payload[field]
        if not isinstance(value, int) or value < -1:
            abort(400, description=f"{field} must be an integer >= -1")
        updates[field] = value

    if all(value is None for value in updates.values()):
        abort(
            400,
            description=f"At least one field is required: {', '.join(updates)}",
        )

    try:
        set_default_quota_settings(
            updates["user_cpu_minutes"],
            updates["user_gpu_minutes"],
            updates["account_cpu_minutes"],
            updates["account_gpu_minutes"],
        )
    except ValueError as exc:
        abort(400, description=str(exc))
    except sqlite3.Error as exc:
        logger.error("set default quotas failed: %s", exc)
        return jsonify({"error": "db_error"}), 500

    logger.info(
        "default quotas: manager=%s updates=%s",
        cast(str, request.user.login),
        {field: value for field, value in updates.items() if value is not None},
    )
    return "", 204


@require_role("admin", "manager")
def set_quota(entity: str, name: str, resource: str) -> Any:
    if resource not in ("cpu", "gpu"):
        abort(400, description="Invalid resource")
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        abort(400, description="Invalid JSON body")
    quota_minutes = payload.get("quota_minutes")
    if not isinstance(quota_minutes, int) or quota_minutes < -1:
        abort(400, description="quota_minutes must be an integer >= -1")
    try:
        if entity == "users":
            _validate_username(name)
            entity_label = "user"
            if resource == "cpu":
                db_set_user_quota(name, quota_minutes)
            else:
                db_set_user_gpu_quota(name, quota_minutes)
        elif entity == "accounts":
            _validate_account(name)
            entity_label = "account"
            if resource == "cpu":
                db_set_account_quota(name, quota_minutes)
            else:
                db_set_account_gpu_quota(name, quota_minutes)
        else:
            abort(404)
    except sqlite3.Error as exc:
        logger.error("set %s %s quota failed: %s", entity_label, resource, exc)
        return jsonify({"error": "db_error"}), 500
    logger.info(
        "quota %s %s: manager=%s name=%s value=%s",
        entity_label,
        resource,
        cast(str, request.user.login),
        name,
        quota_minutes,
    )
    return "", 204


@require_role("admin", "manager")
def adjust_consumption(entity: str, name: str, resource: str) -> Any:
    if entity == "user":
        _validate_username(name)
    elif entity == "account":
        _validate_account(name)
    else:
        abort(404)
    if resource not in ("cpu", "gpu"):
        abort(400, description="Invalid resource")
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        abort(400, description="Invalid JSON body")
    delta_minutes = payload.get("delta_minutes")
    if not isinstance(delta_minutes, int):
        abort(400, description="delta_minutes must be an integer")
    try:
        total = db_adjust_consumed_minutes(entity, name, resource, delta_minutes)
    except ValueError as exc:
        abort(400, description=str(exc))
    except sqlite3.Error as exc:
        logger.error("adjust %s %s consumption failed: %s", entity, resource, exc)
        return jsonify({"error": "db_error"}), 500
    logger.info(
        "consumption %s %s: manager=%s name=%s delta=%+d total=%d",
        entity,
        resource,
        cast(str, request.user.login),
        name,
        delta_minutes,
        total,
    )
    return jsonify({"total_consumed_minutes": total})
