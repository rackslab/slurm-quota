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


def require_role(*roles: Role) -> Callable[[F], F]:
    allowed = set(roles)

    def decorator(view: F) -> F:
        @wraps(view)
        def role_check(*args: Any, **kwargs: Any) -> Any:
            assert current_app.settings is not None
            login = cast(str, request.user.login)
            role = login_role(login)
            if role not in allowed:
                abort(403, description="Insufficient permissions")
            return view(*args, **kwargs)

        return check_jwt(role_check)  # type: ignore[return-value]

    return decorator
