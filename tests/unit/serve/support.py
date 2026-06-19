"""Shared helpers for slurm_quota.serve unit tests."""

from __future__ import annotations

import textwrap
from pathlib import Path

from rfl.authentication.jwt import JWTManager
from rfl.authentication.user import AuthenticatedUser

from slurm_quota.serve.app import SlurmQuotaServeApp
from slurm_quota.serve.settings import conf_defs_path, load_serve_settings


def registered_app() -> SlurmQuotaServeApp:
    app = SlurmQuotaServeApp()
    app.register()
    return app


def write_jwt_site_ini(directory: Path) -> Path:
    jwt_key = directory / "jwt.key"
    site_ini = directory / "serve.ini"
    site_ini.write_text(
        textwrap.dedent(
            f"""\
            [authentication]
            method=jwt

            [jwt]
            key={jwt_key}
            create=yes
            create_parent=yes
            """
        ),
        encoding="utf-8",
    )
    return site_ini


def write_ldap_site_ini(directory: Path) -> Path:
    jwt_key = directory / "jwt.key"
    site_ini = directory / "serve.ini"
    site_ini.write_text(
        textwrap.dedent(
            f"""\
            [authentication]
            method=ldap

            [ldap]
            uri=ldap://localhost
            user_base=ou=people,dc=example,dc=org
            group_base=ou=groups,dc=example,dc=org

            [jwt]
            key={jwt_key}
            create=yes
            create_parent=yes
            """
        ),
        encoding="utf-8",
    )
    return site_ini


def issue_test_token(site_ini: Path, username: str = "alice") -> str:
    settings = load_serve_settings(conf_defs_path(), site_ini)
    jwt = JWTManager.key(
        audience=settings.jwt.audience,
        algorithm=settings.jwt.algorithm,
        path=settings.jwt.key,
        create=settings.jwt.create,
        create_parent=settings.jwt.create_parent,
    )
    user = AuthenticatedUser(login=username, groups=[])
    return jwt.generate(user, settings.jwt.duration)
