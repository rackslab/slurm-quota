# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Local storage for slurm-quota HTTP service authentication tokens."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _xdg_config_home() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if config_home:
        return Path(config_home)
    return Path.home() / ".config"


@dataclass(frozen=True)
class TokenPayload:
    """Decoded JWT payload claims."""

    claims: dict[str, Any]

    def username(self) -> str:
        """
        Return the username claim from the JWT payload.

        Raises:
            ValueError: When the username claim is missing.
        """
        username = self.claims.get("login") or self.claims.get("sub")
        if not isinstance(username, str) or not username:
            raise ValueError("missing username claim")
        return username

    def expiry(self) -> str:
        """
        Format the JWT exp claim for display in the local timezone.

        Returns:
            Formatted timestamp, with (expired) when past.

        Raises:
            ValueError: When the exp claim is missing.
        """
        exp = self.claims.get("exp")
        if not isinstance(exp, int):
            raise ValueError("missing expiration claim")

        dt = datetime.fromtimestamp(exp, tz=timezone.utc).astimezone()
        formatted = dt.strftime("%Y-%m-%d %H:%M:%S %Z")
        if exp < int(datetime.now(tz=timezone.utc).timestamp()):
            return f"{formatted} (expired)"
        return formatted


@dataclass(frozen=True)
class ClientToken:
    """Resolved client JWT and where it was loaded from."""

    value: str
    source: str

    @staticmethod
    def path() -> Path:
        """Return the path to the saved service JWT token file."""
        return _xdg_config_home() / "slurm-quota" / "token"

    @classmethod
    def load(cls, *, env_only: bool = False) -> ClientToken | None:
        """
        Load the effective service JWT and where it comes from.

        Uses SLURM_QUOTA_TOKEN when set, otherwise reads the XDG config file.
        When env_only is True, only SLURM_QUOTA_TOKEN is considered.

        Args:
            env_only: When True, load only from SLURM_QUOTA_TOKEN and raise
                ValueError when it is not set or is empty.

        Returns:
            ClientToken instance, or None when no token is available.

        Raises:
            ValueError: When env_only is True and SLURM_QUOTA_TOKEN is not set
                or is empty.
        """
        env_token = os.environ.get("SLURM_QUOTA_TOKEN", "").strip()
        if env_token:
            return cls(env_token, "SLURM_QUOTA_TOKEN (environment)")

        if env_only:
            raise ValueError("SLURM_QUOTA_TOKEN is not set or is empty")

        path = cls.path()
        if not path.is_file():
            return None

        file_token = path.read_text(encoding="utf-8").strip()
        if not file_token:
            return None

        return cls(file_token, str(path))

    @classmethod
    def load_value(cls) -> str | None:
        """
        Load the effective service JWT string for HTTP API authentication.

        Returns:
            Token string, or None when no token is available.
        """
        client_token = cls.load()
        if client_token is None:
            return None
        return client_token.value

    def save(self) -> Path:
        """
        Persist this token under XDG config with restrictive permissions.

        Returns:
            Path to the written token file.
        """
        path = ClientToken.path()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(self.value.strip(), encoding="utf-8")
        path.chmod(0o600)
        return path

    def decode(self) -> TokenPayload:
        """
        Decode the JWT payload without signature verification.

        Returns:
            Parsed payload claims.

        Raises:
            ValueError: When the token format or payload is invalid.
        """
        parts = self.value.strip().split(".")
        if len(parts) != 3:
            raise ValueError("invalid JWT format")

        payload_b64 = parts[1]
        padding = "=" * (-len(payload_b64) % 4)
        try:
            raw = base64.urlsafe_b64decode(payload_b64 + padding)
            claims = json.loads(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError("invalid JWT payload") from exc

        if not isinstance(claims, dict):
            raise ValueError("invalid JWT payload")

        return TokenPayload(claims=claims)
