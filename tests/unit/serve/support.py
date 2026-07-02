"""Shared helpers for slurm_quota.serve unit tests."""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

from rfl.authentication.jwt import JWTManager
from rfl.authentication.user import AuthenticatedUser

from slurm_quota.serve.app import SlurmQuotaServeApp
from slurm_quota.serve.settings import load_serve_settings
from tests.test_support import serve_conf_defs


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

            [authorization]
            admins=
              alice

            [jwt]
            key={jwt_key}
            create=yes
            create_parent=yes
            """
        ),
        encoding="utf-8",
    )
    return site_ini


def write_jwt_tls_site_ini(directory: Path, cert: Path, key: Path) -> Path:
    jwt_key = directory / "jwt.key"
    site_ini = directory / "serve.ini"
    site_ini.write_text(
        textwrap.dedent(
            f"""\
            [authentication]
            method=jwt

            [authorization]
            admins=
              alice

            [jwt]
            key={jwt_key}
            create=yes
            create_parent=yes

            [tls]
            enabled=yes
            cert={cert}
            key={key}
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

            [authorization]
            admins=
              alice

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
    settings = load_serve_settings(serve_conf_defs(), site_ini)
    jwt = JWTManager.key(
        audience=settings.jwt.audience,
        algorithm=settings.jwt.algorithm,
        path=settings.jwt.key,
        create=settings.jwt.create,
        create_parent=settings.jwt.create_parent,
    )
    user = AuthenticatedUser(login=username, groups=[])
    return jwt.generate(user, settings.jwt.duration)


def write_test_tls_certs(directory: Path) -> tuple[Path, Path]:
    cert = directory / "cert.pem"
    key = directory / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
        ],
        check=True,
        capture_output=True,
    )
    return cert, key
