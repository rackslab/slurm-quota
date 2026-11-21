# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""JWT tokens with scopes claim for slurm-quota-serve."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from rfl.authentication.errors import JWTDecodeError
from rfl.authentication.jwt import JWTBaseManager, JWTManager
from rfl.authentication.user import AuthenticatedUser


class TokenUser(AuthenticatedUser):
    """Authenticated user with the scopes of the token.

    Scopes are None when the token has no scopes claim, its access is then
    restricted by the role of its owner only.
    """

    def __init__(
        self,
        login: str,
        groups: list[str],
        *,
        scopes: Iterable[str] | None = None,
    ) -> None:
        super().__init__(login=login, groups=groups)
        self.scopes = None if scopes is None else frozenset(scopes)


class SlurmQuotaJWTManager(JWTManager):
    """JWT manager that decodes the optional scopes claim of tokens, which RFL
    JWTManager drops."""

    def decode(self, token: str) -> TokenUser:
        payload: dict[str, Any] = JWTBaseManager.decode(
            self, token, audience=self.audience
        )
        scopes = payload.get("scopes")
        if scopes is not None and (
            not isinstance(scopes, list)
            or not all(isinstance(scope, str) for scope in scopes)
        ):
            raise JWTDecodeError("Token scopes are invalid")
        return TokenUser(
            login=payload["sub"],
            groups=payload.get("groups", []),
            scopes=scopes,
        )
