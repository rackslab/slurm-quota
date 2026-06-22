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
    grant_api_manager,
    list_users_with_roles,
    query_accounts_aggregate,
    query_users_aggregate,
    revoke_api_manager,
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
