# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Configuration loading and validation for slurm-quota-serve."""

from __future__ import annotations

import os
import site
import sysconfig
from pathlib import Path

from rfl.settings import RuntimeSettings

DEFAULT_SITE_CONFIG = Path("/etc/slurm-quota/serve.ini")
DEFAULT_CONF_DEFS = Path("/usr/share/slurm-quota/conf/serve.yml")


class ServeSetupError(Exception):
    """Server setup failed."""


def conf_defs_path() -> Path:
    """Return the path to the bundled serve.yml settings definition."""
    env_path = os.environ.get("SLURM_QUOTA_SERVE_CONF_DEFS")
    if env_path:
        return Path(env_path)

    repo_conf = Path(__file__).resolve().parents[3] / "conf" / "serve.yml"
    if repo_conf.is_file():
        return repo_conf

    data_conf = Path(sysconfig.get_path("data")) / "slurm-quota" / "conf" / "serve.yml"
    if data_conf.is_file():
        return data_conf

    user_site = site.getusersitepackages()
    if user_site and str(Path(__file__).resolve()).startswith(user_site):
        user_conf = Path(site.getuserbase()) / "slurm-quota" / "conf" / "serve.yml"
        if user_conf.is_file():
            return user_conf

    return DEFAULT_CONF_DEFS


def site_config_path() -> Path:
    """Return the path to the site INI configuration file."""
    env_path = os.environ.get("SLURM_QUOTA_SERVE_CONFIG")
    if env_path:
        return Path(env_path)
    return DEFAULT_SITE_CONFIG


def load_bind_password(settings: RuntimeSettings) -> None:
    password_file = settings.ldap.bind_password_file
    if password_file is None:
        return
    try:
        settings.ldap.bind_password = password_file.read_text().rstrip("\n")
    except OSError as exc:
        raise ServeSetupError(
            f"Unable to read LDAP bind password file {password_file}: {exc}"
        ) from exc


def validate_jwt_key(settings: RuntimeSettings) -> None:
    jwt_key = settings.jwt.key
    if jwt_key.is_file() or settings.jwt.create:
        return
    raise ServeSetupError(
        f"JWT signing key {jwt_key} does not exist and jwt.create is disabled"
    )


def validate_auth_settings(settings: RuntimeSettings) -> None:
    method = settings.authentication.method
    if method == "ldap":
        missing = []
        if settings.ldap.uri is None:
            missing.append("[ldap] uri")
        if settings.ldap.user_base is None:
            missing.append("[ldap] user_base")
        if settings.ldap.group_base is None:
            missing.append("[ldap] group_base")
        if missing:
            raise ServeSetupError(
                "LDAP authentication requires settings: " + ", ".join(missing)
            )
        if settings.ldap.bind_dn is not None and settings.ldap.bind_password is None:
            raise ServeSetupError(
                "LDAP bind_dn is set but bind_password or bind_password_file is missing"
            )
        validate_jwt_key(settings)
        return
    if method == "jwt":
        validate_jwt_key(settings)
        return
    raise ServeSetupError(f"Unsupported authentication method: {method}")


def load_serve_settings(conf_defs: Path, site_config: Path) -> RuntimeSettings:
    """Load and validate serve configuration."""
    settings = RuntimeSettings.yaml_definition(conf_defs)
    if site_config.exists():
        settings.override_ini(site_config)
    validate_auth_settings(settings)
    return settings
