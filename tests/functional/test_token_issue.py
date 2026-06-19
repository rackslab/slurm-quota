"""Functional tests: `slurm-quota-token` command."""

from __future__ import annotations

import tempfile
import textwrap
from pathlib import Path
from unittest.mock import patch

from rfl.authentication.jwt import JWTManager

from slurm_quota.serve.settings import conf_defs_path, load_serve_settings

from tests.functional.functional_base import FunctionalCLIBase
from tests.unit.serve.support import write_jwt_site_ini


class TestTokenIssueCommand(FunctionalCLIBase):
    def test_issues_token_as_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            site_ini = write_jwt_site_ini(Path(tmp))
            argv = [
                "slurm-quota-token",
                "--conf-defs",
                str(conf_defs_path()),
                "--config",
                str(site_ini),
                "alice",
            ]
            with (
                patch(
                    "slurm_quota.serve.token.auth.get_current_user", return_value="root"
                ),
                self.capture_stdout() as out,
            ):
                self.run_token_main(argv)

            token = out.getvalue().strip()
            self.assertTrue(token)

            settings = load_serve_settings(conf_defs_path(), site_ini)
            jwt = JWTManager.key(
                audience=settings.jwt.audience,
                algorithm=settings.jwt.algorithm,
                path=settings.jwt.key,
            )
            user = jwt.decode(token)
            self.assertEqual(user.login, "alice")
            self.assertEqual(user.groups, [])

    def test_respects_custom_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            site_ini = write_jwt_site_ini(Path(tmp))
            argv = [
                "slurm-quota-token",
                "--conf-defs",
                str(conf_defs_path()),
                "--config",
                str(site_ini),
                "--duration",
                "7",
                "bob",
            ]
            with (
                patch(
                    "slurm_quota.serve.token.auth.get_current_user", return_value="root"
                ),
                self.capture_stdout() as out,
            ):
                self.run_token_main(argv)

            token = out.getvalue().strip()
            settings = load_serve_settings(conf_defs_path(), site_ini)
            jwt = JWTManager.key(
                audience=settings.jwt.audience,
                algorithm=settings.jwt.algorithm,
                path=settings.jwt.key,
            )
            user = jwt.decode(token)
            self.assertEqual(user.login, "bob")

    def test_rejects_non_root_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            site_ini = write_jwt_site_ini(Path(tmp))
            argv = [
                "slurm-quota-token",
                "--conf-defs",
                str(conf_defs_path()),
                "--config",
                str(site_ini),
                "alice",
            ]
            with patch(
                "slurm_quota.serve.token.auth.get_current_user",
                return_value="alice",
            ):
                self.run_token_main_exit(argv, 1)

    def test_rejects_ldap_authentication_method(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            jwt_key = tmp_path / "jwt.key"
            site_ini = tmp_path / "serve.ini"
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
            argv = [
                "slurm-quota-token",
                "--conf-defs",
                str(conf_defs_path()),
                "--config",
                str(site_ini),
                "alice",
            ]
            with patch(
                "slurm_quota.serve.token.auth.get_current_user",
                return_value="root",
            ):
                self.run_token_main_exit(argv, 1)
