# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Local storage for slurm-quota HTTP service authentication tokens."""

from __future__ import annotations

import os
from pathlib import Path


def _xdg_config_home() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if config_home:
        return Path(config_home)
    return Path.home() / ".config"


def service_token_path() -> Path:
    """Return the path to the saved service JWT token file."""
    return _xdg_config_home() / "slurm-quota" / "token"


def save_service_token(token: str) -> Path:
    """
    Persist a service JWT token under XDG config with restrictive permissions.

    Args:
        token: JWT string returned by the HTTP service login endpoint.

    Returns:
        Path to the written token file.
    """
    path = service_token_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(token.strip(), encoding="utf-8")
    path.chmod(0o600)
    return path


def load_service_token() -> str | None:
    """
    Load a service JWT token for HTTP API authentication.

    Uses SLURM_QUOTA_TOKEN when set, otherwise reads the XDG config file.

    Returns:
        Token string, or None when no token is available.
    """
    env_token = os.environ.get("SLURM_QUOTA_TOKEN", "").strip()
    if env_token:
        return env_token

    path = service_token_path()
    if not path.is_file():
        return None

    return path.read_text(encoding="utf-8").strip() or None
