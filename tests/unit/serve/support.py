"""Shared helpers for slurm_quota.serve unit tests."""

from __future__ import annotations

import textwrap
from pathlib import Path

from slurm_quota.serve.app import SlurmQuotaServeApp


def registered_app() -> SlurmQuotaServeApp:
    app = SlurmQuotaServeApp()
    app.register()
    return app


def write_auth_site_ini(directory: Path) -> Path:
    jwt_key = directory / "jwt.key"
    site_ini = directory / "serve.ini"
    site_ini.write_text(
        textwrap.dedent(
            f"""\
            [authentication]
            enabled=yes

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
