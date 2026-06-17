# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Authentication helpers for slurm-quota-serve HTTP routes."""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar

from flask import current_app
from rfl.web.tokens import check_jwt

F = TypeVar("F", bound=Callable[..., Any])


def require_jwt_if_auth_enabled(view: F) -> F:
    """
    Require a valid JWT when REST API authentication is enabled.

    Delegates to RFL check_jwt when the application authentifier is configured;
    otherwise calls the view without token validation.
    """
    jwt_protected = check_jwt(view)

    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if getattr(current_app, "authentifier", None) is not None:
            return jwt_protected(*args, **kwargs)
        return view(*args, **kwargs)

    return wrapped  # type: ignore[return-value]
