# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""HTTP API route handlers for slurm-quota-serve."""

from __future__ import annotations

import dataclasses
import logging
import os
import sqlite3
import time
import uuid
from typing import TYPE_CHECKING, Any, cast

from flask import abort, current_app, g, jsonify, request
from rfl.authentication.errors import LDAPAuthenticationError

import slurm_quota
from slurm_quota.database import (
    active_app_token_exists,
    adjust_consumed_seconds as db_adjust_consumed_seconds,
    append_account_comment,
    append_user_comment,
    connect_database,
    create_app_token,
    get_app_token,
    get_default_quota_settings,
    grant_manager as db_grant_manager,
    grant_manager_account as db_grant_manager_account,
    grant_operator as db_grant_operator,
    is_manager,
    list_account_users,
    list_app_tokens,
    list_manager_accounts,
    list_user_accounts,
    list_users_with_roles,
    load_gpu_factors,
    query_accounts_aggregate,
    query_consumption_breakdown,
    query_users_aggregate,
    revoke_app_token,
    revoke_manager as db_revoke_manager,
    revoke_manager_account as db_revoke_manager_account,
    revoke_operator as db_revoke_operator,
    set_account_comment as db_set_account_comment,
    set_account_gpu_quota as db_set_account_gpu_quota,
    set_account_quota as db_set_account_quota,
    set_default_quota_settings,
    set_gpu_factor as db_set_gpu_factor,
    set_user_comment as db_set_user_comment,
    set_user_gpu_quota as db_set_user_gpu_quota,
    set_user_quota as db_set_user_quota,
)
from slurm_quota.scopes import STATS_SCOPES, scopes_for_role, unknown_scopes
from slurm_quota.serve.authorization import (
    authorize,
    config_admins,
    login_role,
    require_user_token,
)
from slurm_quota.serve.jwt import unverified_claims

if TYPE_CHECKING:
    from slurm_quota.serve.app import SlurmQuotaServeApp

logger = logging.getLogger("slurm_quota")

_MAX_REASON_LENGTH = 500

_MAX_COMMENT_LENGTH = 1000

_TRUE_QUERY_VALUES = ("1", "true", "yes")


def _slurm_user_accounts(username: str) -> set[str]:
    """Return the Slurm accounts of username from the associations cache."""
    with connect_database() as conn:
        return list_user_accounts(conn, username)


@dataclasses.dataclass(frozen=True)
class StatsVisibility:
    """Users, accounts and user/account pairs the viewer of stats may see.

    Attributes:
        viewer: None when the viewer may see everything. Otherwise, the
            username of the viewer.
        users: Users the viewer may see, None for all users.
        accounts: Accounts the viewer may see, None for all accounts.
        managed_accounts: Accounts whose users and pairs the viewer may see, in
            addition to their own.
    """

    viewer: str | None = None
    users: set[str] | None = None
    accounts: set[str] | None = None
    managed_accounts: set[str] = dataclasses.field(default_factory=set)

    @classmethod
    def for_level(cls, login: str, level: str) -> StatsVisibility:
        """Return the visibility of login with the given stats scope.

        Args:
            login: Username of the authenticated user.
            level: stats:read:all for all stats, stats:read:managed for own
                stats and stats of managed accounts, stats:read:own for own
                stats.
        """
        if level == "stats:read:all":
            return cls()
        if level == "stats:read:managed":
            with connect_database() as conn:
                managed = set(list_manager_accounts(conn, login))
                own_accounts = list_user_accounts(conn, login)
                members: set[str] = {login}
                for account in managed:
                    members.update(list_account_users(conn, account))
            return cls(
                viewer=login,
                users=members,
                accounts=managed | own_accounts,
                managed_accounts=managed,
            )
        return cls(viewer=login, users={login}, accounts=_slurm_user_accounts(login))

    def can_view_account(self, account: str) -> bool:
        """Return whether the viewer may see stats of the account."""
        return self.accounts is None or account in self.accounts

    def can_view_pair(self, pair: dict[str, Any]) -> bool:
        """Return whether the viewer may see the consumption of the
        user/account pair."""
        if self.viewer is None:
            return True
        return pair["username"] == self.viewer or (
            pair["account"] in self.managed_accounts
        )

    def user_accounts(self, username: str) -> set[str] | None:
        """Return the accounts shown with the stats of a user, None when the
        viewer may not see the user.

        Viewers see all the accounts of users when they may see everything, and
        their own visible accounts with their own stats. With other users, they
        see the managed accounts of these users.
        """
        if self.viewer is None:
            return _slurm_user_accounts(username)
        assert self.accounts is not None
        if username == self.viewer:
            return self.accounts
        if not self.managed_accounts:
            return None
        accounts = self.managed_accounts.intersection(_slurm_user_accounts(username))
        return accounts or None


def _valid_name(value: str) -> bool:
    """Return whether value is a usable user or account name.

    Invalid names are empty, contain / (which would split a URL path
    segment), or contain whitespace or ASCII control characters. Other
    punctuation, including @, is accepted.
    """
    if not value or "/" in value:
        return False
    return not any(ch.isspace() or ord(ch) < 32 for ch in value)


def _validate_username(username: str) -> None:
    if not _valid_name(username):
        abort(
            400,
            description="Invalid username",
        )


def _validate_account(account: str) -> None:
    if not _valid_name(account):
        abort(
            400,
            description="Invalid account",
        )


def _parse_optional_reason(payload: dict[str, Any]) -> str | None:
    reason = payload.get("reason")
    if reason is None:
        return None
    if not isinstance(reason, str):
        abort(400, description="reason must be a string")
    reason = reason.strip()
    if not reason:
        abort(400, description="reason must not be empty")
    if "\n" in reason or "\r" in reason:
        abort(400, description="reason must be a single line")
    if len(reason) > _MAX_REASON_LENGTH:
        abort(
            400,
            description=f"reason must be at most {_MAX_REASON_LENGTH} characters",
        )
    return reason


def _parse_append_reason(payload: dict[str, Any], reason: str | None) -> str | None:
    """Return the text to append to comments, the reason unless appending is
    disabled with append_reason, None without reason."""
    append_reason = payload.get("append_reason", True)
    if not isinstance(append_reason, bool):
        abort(400, description="append_reason must be a boolean")
    return reason if append_reason else None


def _parse_comment(payload: dict[str, Any]) -> str | None:
    """Return the comment of payload, None to clear the comment."""
    if "comment" not in payload:
        abort(400, description="comment is required")
    comment = payload["comment"]
    if comment is None:
        return None
    if not isinstance(comment, str):
        abort(400, description="comment must be a string or null")
    comment = comment.strip()
    if "\n" in comment or "\r" in comment:
        abort(400, description="comment must be a single line")
    if len(comment) > _MAX_COMMENT_LENGTH:
        abort(
            400,
            description=f"comment must be at most {_MAX_COMMENT_LENGTH} characters",
        )
    return comment or None


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


@authorize(*STATS_SCOPES)
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
    include_breakdown = (
        request.args.get("breakdown") or ""
    ).strip().lower() in _TRUE_QUERY_VALUES

    level = next(scope for scope in reversed(STATS_SCOPES) if scope in g.scopes)
    visibility = StatsVisibility.for_level(cast(str, request.user.login), level)

    # Users and accounts of stats, and users and accounts selecting the pairs
    # of the consumption breakdown, as in query_consumption_breakdown(). None
    # selects all of them.
    usernames: set[str] | None
    accounts: set[str] | None
    breakdown_usernames: set[str] | None
    breakdown_accounts: set[str] | None
    if account_param:
        # Only this account, with the consumption of each visible user on it.
        if not visibility.can_view_account(account_param):
            abort(403, description="Not allowed to view stats for this account")
        usernames, accounts = set(), {account_param}
        breakdown_usernames, breakdown_accounts = set(), {account_param}
    elif username_param:
        # This user and their visible accounts, with the consumption of the
        # user on each account.
        user_accounts = visibility.user_accounts(username_param)
        if user_accounts is None:
            abort(403, description="Not allowed to view stats for other users")
        usernames, accounts = {username_param}, user_accounts
        breakdown_usernames, breakdown_accounts = {username_param}, set()
    elif visibility.viewer is None:
        usernames = accounts = breakdown_usernames = breakdown_accounts = None
    else:
        # All visible users and accounts, with the visible consumption.
        usernames, accounts = visibility.users, visibility.accounts
        breakdown_usernames = {visibility.viewer}
        breakdown_accounts = visibility.managed_accounts

    payload: dict[str, Any] = {"users": [], "accounts": []}
    if include_breakdown:
        payload["breakdown"] = []
    if not os.path.exists(slurm_quota.DB_PATH):
        return jsonify(payload)
    try:
        with connect_database() as conn:
            payload["users"] = query_users_aggregate(conn, usernames)
            payload["accounts"] = query_accounts_aggregate(conn, accounts)
            if include_breakdown:
                payload["breakdown"] = [
                    pair
                    for pair in query_consumption_breakdown(
                        conn, breakdown_usernames, breakdown_accounts
                    )
                    if visibility.can_view_pair(pair)
                ]
    except sqlite3.Error as exc:
        logger.error("/stats query failed: %s", exc)
        return jsonify({"error": "db_error"}), 500
    return jsonify(payload)


@authorize()
def me() -> Any:
    login = cast(str, request.user.login)
    role = login_role(login)
    payload: dict[str, Any] = {"username": login, "role": role}
    if role == "manager":
        with connect_database() as conn:
            payload["accounts"] = list_manager_accounts(conn, login)
    return jsonify(payload)


@authorize("roles:read")
def list_roles() -> Any:
    serve_app = cast("SlurmQuotaServeApp", current_app)
    assert serve_app.settings is not None
    with connect_database() as conn:
        users = list_users_with_roles(conn, config_admins(serve_app.settings))
    return jsonify({"users": users})


@authorize("roles:write")
def grant_operator(username: str) -> Any:
    _validate_username(username)
    serve_app = cast("SlurmQuotaServeApp", current_app)
    assert serve_app.settings is not None
    if username in config_admins(serve_app.settings):
        return "", 204
    with connect_database() as conn:
        db_grant_operator(conn, username)
    return "", 204


@authorize("roles:write")
def revoke_operator(username: str) -> Any:
    _validate_username(username)
    with connect_database() as conn:
        db_revoke_operator(conn, username)
    return "", 204


@authorize("roles:write")
def grant_manager(username: str) -> Any:
    _validate_username(username)
    serve_app = cast("SlurmQuotaServeApp", current_app)
    assert serve_app.settings is not None
    if username in config_admins(serve_app.settings):
        return "", 204
    with connect_database() as conn:
        db_grant_manager(conn, username)
    return "", 204


@authorize("roles:write")
def revoke_manager(username: str) -> Any:
    _validate_username(username)
    with connect_database() as conn:
        db_revoke_manager(conn, username)
    return "", 204


@authorize("roles:read")
def list_manager_accounts_route(username: str) -> Any:
    _validate_username(username)
    with connect_database() as conn:
        if not is_manager(conn, username):
            abort(404, description="User is not a manager")
        accounts = list_manager_accounts(conn, username)
    return jsonify({"accounts": accounts})


@authorize("roles:write")
def grant_manager_account_route(username: str, account: str) -> Any:
    _validate_username(username)
    _validate_account(account)
    with connect_database() as conn:
        if not is_manager(conn, username):
            abort(404, description="User is not a manager")
        db_grant_manager_account(conn, username, account)
    return "", 204


@authorize("roles:write")
def revoke_manager_account_route(username: str, account: str) -> Any:
    _validate_username(username)
    _validate_account(account)
    with connect_database() as conn:
        if not is_manager(conn, username):
            abort(404, description="User is not a manager")
        db_revoke_manager_account(conn, username, account)
    return "", 204


@authorize("quotas:read")
def get_default_quotas() -> Any:
    try:
        with connect_database() as conn:
            settings = get_default_quota_settings(conn)
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


@authorize("quotas:write")
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


@authorize("quotas:write")
def set_quota(entity: str, name: str, resource: str) -> Any:
    if resource not in ("cpu", "gpu"):
        abort(400, description="Invalid resource")
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        abort(400, description="Invalid JSON body")
    quota_minutes = payload.get("quota_minutes")
    if not isinstance(quota_minutes, int) or quota_minutes < -1:
        abort(400, description="quota_minutes must be an integer >= -1")
    reason = _parse_optional_reason(payload)
    append_comment = _parse_append_reason(payload, reason)
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
        "quota %s %s: manager=%s name=%s value=%s reason=%s",
        entity_label,
        resource,
        cast(str, request.user.login),
        name,
        quota_minutes,
        "n/a" if reason is None else repr(reason),
    )
    if append_comment:
        try:
            if entity == "users":
                append_user_comment(name, append_comment)
            else:
                append_account_comment(name, append_comment)
        except sqlite3.Error as exc:
            logger.error("append reason to %s comment failed: %s", entity_label, exc)
            return jsonify({"error": "db_error"}), 500
    return "", 204


@authorize("comments:write")
def set_comment(entity: str, name: str) -> Any:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        abort(400, description="Invalid JSON body")
    comment = _parse_comment(payload)
    if entity == "users":
        _validate_username(name)
        entity_label = "user"
        setter = db_set_user_comment
    elif entity == "accounts":
        _validate_account(name)
        entity_label = "account"
        setter = db_set_account_comment
    else:
        abort(404)
    try:
        setter(name, comment)
    except sqlite3.Error as exc:
        logger.error("set %s comment failed: %s", entity_label, exc)
        return jsonify({"error": "db_error"}), 500
    logger.info(
        "comment %s: manager=%s name=%s comment=%s",
        entity_label,
        cast(str, request.user.login),
        name,
        "n/a" if comment is None else repr(comment),
    )
    return "", 204


@authorize("quotas:write")
def adjust_consumption(username: str, account: str, resource: str) -> Any:
    _validate_username(username)
    _validate_account(account)
    if resource not in ("cpu", "gpu"):
        abort(400, description="Invalid resource")
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        abort(400, description="Invalid JSON body")
    delta_seconds = payload.get("delta_seconds")
    if not isinstance(delta_seconds, int):
        abort(400, description="delta_seconds must be an integer")
    reason = _parse_optional_reason(payload)
    append_comment = _parse_append_reason(payload, reason)
    try:
        total = db_adjust_consumed_seconds(username, account, resource, delta_seconds)
    except ValueError as exc:
        abort(400, description=str(exc))
    except sqlite3.Error as exc:
        logger.error("adjust %s consumption failed: %s", resource, exc)
        return jsonify({"error": "db_error"}), 500
    logger.info(
        "consumption %s: manager=%s user=%s account=%s delta=%+ds total=%ds reason=%s",
        resource,
        cast(str, request.user.login),
        username,
        account,
        delta_seconds,
        total,
        "n/a" if reason is None else repr(reason),
    )
    if append_comment:
        try:
            append_user_comment(username, append_comment)
            append_account_comment(account, append_comment)
        except sqlite3.Error as exc:
            logger.error("append reason to comments failed: %s", exc)
            return jsonify({"error": "db_error"}), 500
    return jsonify({"total_consumed_seconds": total})


@authorize("quotas:read")
def get_gpu_factors() -> Any:
    try:
        factors = load_gpu_factors()
    except sqlite3.Error as exc:
        logger.error("get gpu factors failed: %s", exc)
        return jsonify({"error": "db_error"}), 500
    default_factor = factors.pop("__default__", 1.0)
    return jsonify({"default_factor": default_factor, "factors": factors})


@authorize("quotas:write")
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


def _app_token_status(token: dict[str, Any], now: int) -> str:
    if token["revoked_at"] is not None:
        return "revoked"
    if token["expires_at"] <= now:
        return "expired"
    return "active"


def _app_token_payload(token: dict[str, Any], now: int) -> dict[str, Any]:
    return {**token, "status": _app_token_status(token, now)}


def _parse_app_token_scopes(payload: dict[str, Any], role: str) -> list[str]:
    scopes = payload.get("scopes")
    if (
        not isinstance(scopes, list)
        or not scopes
        or not all(isinstance(scope, str) for scope in scopes)
    ):
        abort(400, description="scopes must be a non-empty list of strings")
    unknown = unknown_scopes(scopes)
    if unknown:
        abort(400, description=f"Unknown scopes: {', '.join(sorted(set(unknown)))}")
    allowed = scopes_for_role(role)
    denied = sorted({scope for scope in scopes if scope not in allowed})
    if denied:
        abort(403, description=f"Scopes not allowed for {role}: {', '.join(denied)}")
    return [scope for scope in allowed if scope in scopes]


def _parse_app_token_duration(payload: dict[str, Any]) -> int:
    serve_app = cast("SlurmQuotaServeApp", current_app)
    assert serve_app.settings is not None
    duration = payload.get("duration", serve_app.settings.jwt.app_duration)
    max_duration = serve_app.settings.jwt.app_max_duration
    if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
        abort(400, description="duration must be a positive integer")
    if duration > max_duration:
        abort(400, description=f"duration must be at most {max_duration} days")
    return duration


@require_user_token
def create_app_token_route() -> Any:
    serve_app = cast("SlurmQuotaServeApp", current_app)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        abort(400, description="Invalid JSON body")
    name = payload.get("name")
    if not isinstance(name, str) or not _valid_name(name):
        abort(400, description="Invalid token name")
    login = cast(str, request.user.login)
    role = login_role(login)
    scopes = _parse_app_token_scopes(payload, role)
    duration = _parse_app_token_duration(payload)

    token_id = uuid.uuid4().hex
    now = int(time.time())
    with connect_database() as conn:
        if active_app_token_exists(conn, login, name, now):
            abort(400, description=f"An active token named {name} already exists")
        token = serve_app.jwt.generate_application(
            request.user, duration, token_id, scopes
        )
        # Record issue and expiration times of the token claims, which are
        # enforced when the token is decoded.
        claims = unverified_claims(token)
        created_at, expires_at = claims["iat"], claims["exp"]
        create_app_token(conn, token_id, login, name, scopes, created_at, expires_at)
    logger.info(
        "application token created: user=%s id=%s name=%s scopes=%s duration=%dd",
        login,
        token_id,
        name,
        ",".join(scopes),
        duration,
    )
    response = _app_token_payload(
        {
            "id": token_id,
            "username": login,
            "name": name,
            "scopes": scopes,
            "created_at": created_at,
            "expires_at": expires_at,
            "revoked_at": None,
        },
        now,
    )
    response["token"] = token
    return jsonify(response), 201


@require_user_token
def list_app_tokens_route() -> Any:
    login = cast(str, request.user.login)
    username_param = (request.args.get("username") or "").strip() or None
    all_param = (request.args.get("all") or "").strip().lower() in _TRUE_QUERY_VALUES
    if username_param and all_param:
        abort(400, description="username and all are mutually exclusive")
    username: str | None = login
    if username_param or all_param:
        if login_role(login) != "admin":
            abort(403, description="Not allowed to view tokens of other users")
        username = username_param
    if username is not None:
        _validate_username(username)
    now = int(time.time())
    with connect_database() as conn:
        tokens = list_app_tokens(conn, username)
    return jsonify({"tokens": [_app_token_payload(token, now) for token in tokens]})


@require_user_token
def revoke_app_token_route(token_id: str) -> Any:
    login = cast(str, request.user.login)
    with connect_database() as conn:
        token = get_app_token(conn, token_id)
        if token is None or (
            token["username"] != login and login_role(login) != "admin"
        ):
            abort(404, description="Token not found")
        revoke_app_token(conn, token_id, int(time.time()))
    logger.info(
        "application token revoked: user=%s id=%s name=%s owner=%s",
        login,
        token_id,
        token["name"],
        token["username"],
    )
    return "", 204
