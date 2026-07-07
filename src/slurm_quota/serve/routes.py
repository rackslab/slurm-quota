# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""HTTP API route handlers for slurm-quota-serve."""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from typing import TYPE_CHECKING, Any, cast

from flask import abort, current_app, jsonify, request
from rfl.authentication.errors import LDAPAuthenticationError
from rfl.web.tokens import check_jwt

import slurm_quota
from slurm_quota import slurm as slurm_integration
from slurm_quota.database import (
    adjust_consumed_minutes as db_adjust_consumed_minutes,
    connect_database,
    get_default_quota_settings,
    grant_manager as db_grant_manager,
    grant_manager_account as db_grant_manager_account,
    grant_operator as db_grant_operator,
    is_manager,
    list_manager_accounts,
    list_users_with_roles,
    load_gpu_factors,
    query_accounts_aggregate,
    query_users_aggregate,
    revoke_manager as db_revoke_manager,
    revoke_manager_account as db_revoke_manager_account,
    revoke_operator as db_revoke_operator,
    set_account_gpu_quota as db_set_account_gpu_quota,
    set_account_quota as db_set_account_quota,
    set_default_quota_settings,
    set_gpu_factor as db_set_gpu_factor,
    set_user_gpu_quota as db_set_user_gpu_quota,
    set_user_quota as db_set_user_quota,
)
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
    usernames_filter: set[str] | None = None,
    accounts_filter: set[str] | None = None,
) -> tuple[dict[str, Any] | None, tuple[Any, int] | None]:
    """Load aggregated user and account consumption stats from the database.

    Applies optional username and account filters before querying. When the
    database file is missing, returns empty lists without error. When exactly
    one username is requested and no account filter is set, account stats are
    scoped to that user's Slurm accounts.

    Args:
        usernames_filter: Usernames to include. None selects all users, an empty
            set selects none.
        accounts_filter: Accounts to include. None selects all accounts unless
            a single username is given, in which case that user's Slurm accounts
            are used. An empty set selects none.

    Returns:
        A pair of payload and error response. On success the payload is a dict
        with users and accounts keys and the error is None. On database failure
        the payload is None and the error is an HTTP 500 response tuple.
    """
    try:
        if not os.path.exists(slurm_quota.DB_PATH):
            return {"users": [], "accounts": []}, None

        accounts_query = accounts_filter
        if (
            accounts_query is None
            and usernames_filter is not None
            and len(usernames_filter) == 1
        ):
            username = next(iter(usernames_filter))
            try:
                accounts_query = slurm_integration.get_user_accounts(username)
            except Exception:
                accounts_query = set()

        with connect_database() as conn:
            users = query_users_aggregate(conn, usernames_filter)
            accounts = query_accounts_aggregate(conn, accounts_query)
        return {"users": users, "accounts": accounts}, None
    except sqlite3.Error as exc:
        logger.error("/stats query failed: %s", exc)
        return None, (jsonify({"error": "db_error"}), 500)


def _resolve_manager_stats_scope(
    login: str,
    username_param: str | None,
    account_param: str | None,
) -> tuple[set[str] | None, set[str] | None]:
    """Build username and account filters for a manager viewing stats.

    Managers may only see users and accounts within their assigned scope.
    Query parameters narrow that scope; requests outside it raise HTTP 403.

    Args:
        login: Username of the authenticated manager.
        username_param: Optional username query parameter, or None for no filter.
        account_param: Optional account query parameter, or None for no filter.

    Returns:
        A pair of usernames_filter and accounts_filter suitable for fetch_stats.
        With no assigned accounts, both filters are empty sets. With an account
        parameter, only that account is returned when it is assigned. With a
        username parameter, only that user is returned when they belong to an
        assigned account; account stats are limited to the overlap between the
        user's Slurm accounts and the assigned set. With neither parameter, users
        are all members of assigned accounts and accounts are the full assigned
        set.
    """
    with connect_database() as conn:
        assigned = set(list_manager_accounts(conn, login))

    if not assigned:
        return set(), assigned

    if account_param:
        if account_param not in assigned:
            abort(403, description="Not allowed to view stats for this account")
        return set(), {account_param}

    if username_param:
        try:
            user_accounts = slurm_integration.get_user_accounts(username_param)
        except Exception:
            user_accounts = set()
        if not user_accounts.intersection(assigned):
            abort(403, description="Not allowed to view stats for other users")
        return {username_param}, assigned.intersection(user_accounts)

    members: set[str] = set()
    for account in assigned:
        members.update(slurm_integration.get_account_users(account))
    return members, assigned


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
    except LDAPAuthenticationError as exc:
        logger.error("LDAP authentication failed: %s", exc)
        abort(401, description="Authentication failed")

    token = serve_app.jwt.generate(user, serve_app.settings.jwt.duration)
    return jsonify(
        {"token": token, "username": user.login, "role": login_role(user.login)}
    )


@check_jwt
def stats() -> Any:
    username_param = (request.args.get("username") or "").strip() or None
    account_param = (request.args.get("account") or "").strip() or None
    if username_param and account_param:
        return (
            jsonify(
                {
                    "error": "bad_request",
                    "message": "username and account are mutually exclusive",
                }
            ),
            400,
        )

    login = cast(str, request.user.login)
    role = login_role(login)
    usernames_filter: set[str] | None = None
    accounts_filter: set[str] | None = None

    if role == "manager":
        usernames_filter, accounts_filter = _resolve_manager_stats_scope(
            login, username_param, account_param
        )
    elif role not in ("admin", "operator"):
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
        if account_param:
            usernames_filter = set()
            accounts_filter = {account_param}
        elif username_param:
            usernames_filter = {username_param}
    elif account_param:
        usernames_filter = set()
        accounts_filter = {account_param}
    elif username_param:
        usernames_filter = {username_param}

    payload, error = fetch_stats(usernames_filter, accounts_filter)
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
    with connect_database() as conn:
        users = list_users_with_roles(conn, config_admins(serve_app.settings))
    return jsonify({"users": users})


@require_role("admin")
def grant_operator(username: str) -> Any:
    _validate_username(username)
    serve_app = cast("SlurmQuotaServeApp", current_app)
    assert serve_app.settings is not None
    if username in config_admins(serve_app.settings):
        return "", 204
    with connect_database() as conn:
        db_grant_operator(conn, username)
    return "", 204


@require_role("admin")
def revoke_operator(username: str) -> Any:
    _validate_username(username)
    with connect_database() as conn:
        db_revoke_operator(conn, username)
    return "", 204


@require_role("admin")
def grant_manager(username: str) -> Any:
    _validate_username(username)
    serve_app = cast("SlurmQuotaServeApp", current_app)
    assert serve_app.settings is not None
    if username in config_admins(serve_app.settings):
        return "", 204
    with connect_database() as conn:
        db_grant_manager(conn, username)
    return "", 204


@require_role("admin")
def revoke_manager(username: str) -> Any:
    _validate_username(username)
    with connect_database() as conn:
        db_revoke_manager(conn, username)
    return "", 204


@require_role("admin")
def list_manager_accounts_route(username: str) -> Any:
    _validate_username(username)
    with connect_database() as conn:
        if not is_manager(conn, username):
            abort(404, description="User is not a manager")
        accounts = list_manager_accounts(conn, username)
    return jsonify({"accounts": accounts})


@require_role("admin")
def grant_manager_account_route(username: str, account: str) -> Any:
    _validate_username(username)
    _validate_account(account)
    with connect_database() as conn:
        if not is_manager(conn, username):
            abort(404, description="User is not a manager")
        db_grant_manager_account(conn, username, account)
    return "", 204


@require_role("admin")
def revoke_manager_account_route(username: str, account: str) -> Any:
    _validate_username(username)
    _validate_account(account)
    with connect_database() as conn:
        if not is_manager(conn, username):
            abort(404, description="User is not a manager")
        db_revoke_manager_account(conn, username, account)
    return "", 204


@require_role("admin", "operator")
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


@require_role("admin", "operator")
def set_default_quotas() -> Any:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        abort(400, description="Invalid JSON body")

    updates: dict[str, int | None] = {
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


@require_role("admin", "operator")
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


@require_role("admin", "operator")
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


@require_role("admin", "operator")
def get_gpu_factors() -> Any:
    try:
        factors = load_gpu_factors()
    except sqlite3.Error as exc:
        logger.error("get gpu factors failed: %s", exc)
        return jsonify({"error": "db_error"}), 500
    default_factor = factors.pop("__default__", 1.0)
    return jsonify({"default_factor": default_factor, "factors": factors})


@require_role("admin", "operator")
def set_gpu_factor(gpu_type: str) -> Any:
    if not gpu_type:
        abort(400, description="Invalid gpu_type")
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        abort(400, description="Invalid JSON body")
    factor = payload.get("factor")
    if not isinstance(factor, (int, float)) or factor <= 0:
        abort(400, description="factor must be a positive number")
    try:
        db_set_gpu_factor(gpu_type, float(factor))
    except sqlite3.Error as exc:
        logger.error("set gpu factor failed: %s", exc)
        return jsonify({"error": "db_error"}), 500
    logger.info(
        "gpu factor: manager=%s gpu_type=%s factor=%s",
        cast(str, request.user.login),
        gpu_type,
        factor,
    )
    return "", 204
