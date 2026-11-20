# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Role-based authorization for slurm-quota-serve HTTP routes."""

from __future__ import annotations

import sqlite3
from functools import wraps
from typing import Any, Callable, Literal, TypeVar, cast

from flask import abort, current_app, request
from rfl.settings import RuntimeSettings
from rfl.web.tokens import check_jwt

from slurm_quota.database import connect_database, is_manager, is_operator
from slurm_quota.scopes import SCOPE_ROLES, SCOPES
from slurm_quota.serve.jwt import TokenUser

Role = Literal["admin", "operator", "manager", "user"]

F = TypeVar("F", bound=Callable[..., Any])


def config_admins(settings: RuntimeSettings) -> set[str]:
    admins = getattr(settings.authorization, "admins", None)
    if not admins:
        return set()
    return set(admins)


def resolve_role(
    login: str, settings: RuntimeSettings, conn: sqlite3.Connection
) -> Role:
    if login in config_admins(settings):
        return "admin"
    if is_operator(conn, login):
        return "operator"
    if is_manager(conn, login):
        return "manager"
    return "user"


def login_role(username: str) -> Role:
    assert current_app.settings is not None
    with connect_database() as conn:
        return resolve_role(username, current_app.settings, conn)


def authorize(scope: str) -> Callable[[F], F]:
    """Require a valid token with the given scope.

    The scope must be available for the current role of the token owner, and
    carried by the token when it has a scopes claim.
    """
    assert scope in SCOPES, f"unknown scope {scope}"
    roles = SCOPE_ROLES[scope]

    def decorator(view: F) -> F:
        @wraps(view)
        def authorization_check(*args: Any, **kwargs: Any) -> Any:
            assert current_app.settings is not None
            user = cast(TokenUser, request.user)
            if login_role(user.login) not in roles:
                abort(403, description="Insufficient permissions")
            if user.scopes is not None and scope not in user.scopes:
                abort(403, description="Token scope insufficient")
            return view(*args, **kwargs)

        return check_jwt(authorization_check)  # type: ignore[return-value]

    return decorator
