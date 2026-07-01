"""Unit tests for slurm_quota.serve.settings."""

from __future__ import annotations

import sysconfig
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import patch

from rfl.authentication.errors import JWTDecodeError, LDAPAuthenticationError

from slurm_quota.serve.app import SlurmQuotaServeApp
from slurm_quota.serve.settings import (
    DEFAULT_CONF_DEFS,
    DEFAULT_SITE_CONFIG,
    ServeSetupError,
    conf_defs_path,
    load_bind_password,
    load_serve_settings,
    site_config_path,
    validate_auth_settings,
    validate_tls_settings,
)
from tests.test_support import SlurmQuotaTestCase
from tests.unit.serve.support import (
    registered_app,
    write_jwt_site_ini,
    write_jwt_tls_site_ini,
    write_test_tls_certs,
)


def _load_settings(defs_path: Path, site_path: Path):
    app = SlurmQuotaServeApp()
    app.load_settings(defs_path, site_path)
    assert app.settings is not None
    return app.settings


class TestConfDefsPath(SlurmQuotaTestCase):
    def test_default_when_env_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            path = conf_defs_path()
        repo_conf = Path(__file__).resolve().parents[3] / "conf" / "serve.yml"
        data_conf = (
            Path(sysconfig.get_path("data")) / "slurm-quota" / "conf" / "serve.yml"
        )
        self.assertIn(path, {repo_conf, data_conf, DEFAULT_CONF_DEFS})
        self.assertTrue(path.is_file(), path)
        self.assertEqual(path.name, "serve.yml")

    def test_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            custom = Path(tmp) / "custom.yml"
            custom.touch()
            with patch.dict("os.environ", {"SLURM_QUOTA_SERVE_CONF_DEFS": str(custom)}):
                self.assertEqual(conf_defs_path(), custom)


class TestSiteConfigPath(SlurmQuotaTestCase):
    def test_default_when_env_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(site_config_path(), DEFAULT_SITE_CONFIG)

    def test_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            custom = Path(tmp) / "custom.ini"
            custom.touch()
            with patch.dict("os.environ", {"SLURM_QUOTA_SERVE_CONFIG": str(custom)}):
                self.assertEqual(site_config_path(), custom)


class TestValidateAuthSettings(SlurmQuotaTestCase):
    def setUp(self):
        super().setUp()
        self.defs_path = conf_defs_path()

    def test_raises_when_ldap_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            site_ini = Path(tmp) / "serve.ini"
            site_ini.write_text(
                "[authentication]\nmethod=ldap\n",
                encoding="utf-8",
            )
            settings = _load_settings(self.defs_path, site_ini)
        with self.assertRaises(ServeSetupError):
            validate_auth_settings(settings)

    def test_accepts_jwt_method(self):
        with tempfile.TemporaryDirectory() as tmp:
            site_ini = write_jwt_site_ini(Path(tmp))
            settings = load_serve_settings(self.defs_path, site_ini)
        self.assertEqual(settings.authentication.method, "jwt")

    def test_reads_bind_password_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            password_file = tmp_path / "ldap_password"
            password_file.write_text("s3cr3t\n", encoding="utf-8")
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
                    bind_dn=cn=system,dc=example,dc=org
                    bind_password_file={password_file}
                    """
                ),
                encoding="utf-8",
            )
            settings = _load_settings(self.defs_path, site_ini)
            load_bind_password(settings)
        self.assertEqual(settings.ldap.bind_password, "s3cr3t")


class TestValidateTlsSettings(SlurmQuotaTestCase):
    def setUp(self):
        super().setUp()
        self.defs_path = conf_defs_path()

    def test_raises_when_enabled_without_cert_and_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            site_ini = Path(tmp) / "serve.ini"
            site_ini.write_text(
                textwrap.dedent(
                    """\
                    [authentication]
                    method=jwt

                    [jwt]
                    key=jwt.key
                    create=yes

                    [tls]
                    enabled=yes
                    """
                ),
                encoding="utf-8",
            )
            settings = _load_settings(self.defs_path, site_ini)
        with self.assertRaises(ServeSetupError):
            validate_tls_settings(settings)

    def test_raises_when_cert_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            key = tmp_path / "key.pem"
            key.write_text("not-a-real-key", encoding="utf-8")
            site_ini = tmp_path / "serve.ini"
            site_ini.write_text(
                textwrap.dedent(
                    f"""\
                    [authentication]
                    method=jwt

                    [jwt]
                    key=jwt.key
                    create=yes

                    [tls]
                    enabled=yes
                    cert={tmp_path / "missing.pem"}
                    key={key}
                    """
                ),
                encoding="utf-8",
            )
            settings = _load_settings(self.defs_path, site_ini)
        with self.assertRaises(ServeSetupError):
            validate_tls_settings(settings)

    def test_accepts_valid_tls_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cert, key = write_test_tls_certs(tmp_path)
            site_ini = tmp_path / "serve.ini"
            site_ini.write_text(
                textwrap.dedent(
                    f"""\
                    [authentication]
                    method=jwt

                    [jwt]
                    key=jwt.key
                    create=yes

                    [tls]
                    enabled=yes
                    cert={cert}
                    key={key}
                    """
                ),
                encoding="utf-8",
            )
            settings = _load_settings(self.defs_path, site_ini)
            validate_tls_settings(settings)

    def test_setup_builds_ssl_context_when_tls_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cert, key = write_test_tls_certs(tmp_path)
            site_ini = write_jwt_tls_site_ini(tmp_path, cert, key)
            app = registered_app()
            with (
                patch("slurm_quota.serve.app.auth.require_slurm_user"),
                patch("slurm_quota.serve.app.init_database"),
            ):
                app.setup(conf_defs_path(), site_ini)
        self.assertIsNotNone(app.ssl_context)


class TestAuthErrors(SlurmQuotaTestCase):
    def test_rfl_error_types_exist(self):
        self.assertTrue(issubclass(JWTDecodeError, Exception))
        self.assertTrue(issubclass(LDAPAuthenticationError, Exception))

    @patch("rfl.authentication.ldap.LDAPAuthentifier")
    def test_login_maps_ldap_errors(self, m_ldap_cls):
        m_ldap_cls.return_value.login.side_effect = LDAPAuthenticationError(
            "Invalid user or password"
        )
        authentifier = m_ldap_cls.return_value
        with self.assertRaises(LDAPAuthenticationError):
            authentifier.login("alice", "wrong")
