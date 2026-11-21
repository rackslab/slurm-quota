# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Role-based authorization for slurm-quota-serve HTTP routes."""

from __future__ import annotations

import sqlite3
from functools import wraps
from typing import Any, Callable, Literal, TypeVar, cast

from flask import abort, current_app, g, request
from rfl.settings import RuntimeSettings
from rfl.web.tokens import check_jwt

from slurm_quota.database import connect_database, is_manager, is_operator
from slurm_quota.scopes import SCOPES, scopes_for_role
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


def authorize(*scopes: str) -> Callable[[F], F]:
    """Require a valid token with one of the given scopes, or only a valid
    token when no scope is given.

    The scopes of a token are the scopes available for the current role of its
    owner, restricted to the scopes carried by the token when it has a scopes
    claim. They are stored in flask.g.scopes for the view.
    """
    for scope in scopes:
        assert scope in SCOPES, f"unknown scope {scope}"

    def decorator(view: F) -> F:
        @wraps(view)
        def authorization_check(*args: Any, **kwargs: Any) -> Any:
            assert current_app.settings is not None
            user = cast(TokenUser, request.user)
            role_scopes = set(scopes_for_role(login_role(user.login)))
            token_scopes = (
                role_scopes if user.scopes is None else role_scopes & user.scopes
            )
            if scopes and role_scopes.isdisjoint(scopes):
                abort(403, description="Insufficient permissions")
            if scopes and token_scopes.isdisjoint(scopes):
                abort(403, description="Token scope insufficient")
            g.scopes = frozenset(token_scopes)
            return view(*args, **kwargs)

        return check_jwt(authorization_check)  # type: ignore[return-value]

    return decorator
