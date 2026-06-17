# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""HTTP API route handlers for slurm-quota-serve."""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple, cast

from flask import abort, current_app, jsonify, request
from rfl.authentication.errors import LDAPAuthenticationError

import slurm_quota
from slurm_quota.database import query_accounts_aggregate, query_users_aggregate
from slurm_quota import slurm as slurm_integration

if TYPE_CHECKING:
    from slurm_quota.serve.app import SlurmQuotaServeApp

logger = logging.getLogger("slurm_quota")


def fetch_stats(
    username_param: Optional[str], account_param: Optional[str]
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
        with sqlite3.connect(slurm_quota.DB_PATH) as conn:
            users = query_users_aggregate(conn, username_param or None)
            accounts_filter: Optional[set[str]] = None
            if username_param:
                try:
                    accounts_filter = slurm_integration.get_user_accounts(
                        username_param
                    )
                except Exception:
                    accounts_filter = set()
            if account_param:
                accounts_filter = {account_param}
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
    if serve_app.authentifier is None or serve_app.settings is None:
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
    return jsonify({"token": token})


def stats() -> Any:
    username_param = (request.args.get("username") or "").strip() or None
    account_param = (request.args.get("account") or "").strip() or None
    payload, error = fetch_stats(username_param, account_param)
    if error is not None:
        response, status = error
        return response, status
    return jsonify(payload)
