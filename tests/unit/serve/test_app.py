"""Unit tests for slurm_quota.serve.app."""

from __future__ import annotations

import os
import ssl
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import patch

from rfl.authentication.user import AuthenticatedUser
from rfl.settings.errors import SettingsDefinitionError

from slurm_quota.database import init_database
from slurm_quota.serve.app import SlurmQuotaServeApp
from slurm_quota.serve.settings import ServeSetupError, conf_defs_path
from tests.test_support import SlurmQuotaTestCase
from tests.unit.serve.support import (
    write_jwt_site_ini,
    write_jwt_tls_site_ini,
    write_test_tls_certs,
)


class TestLoadSettings(SlurmQuotaTestCase):
    def setUp(self):
        super().setUp()
        self.defs_path = conf_defs_path()

    def test_defaults_use_ldap_method(self):
        app = SlurmQuotaServeApp()
        app.load_settings(self.defs_path, Path("/no/such/site.ini"))
        assert app.settings is not None
        self.assertEqual(app.settings.authentication.method, "ldap")
        self.assertEqual(app.settings.jwt.audience, "slurm-quota")

    def test_site_ini_overrides_method_and_uri(self):
        with tempfile.TemporaryDirectory() as tmp:
            site_ini = Path(tmp) / "serve.ini"
            site_ini.write_text(
                textwrap.dedent(
                    """\
                    [authentication]
                    method=ldap

                    [ldap]
                    uri=ldap://ldap.example.org
                    user_base=ou=people,dc=example,dc=org
                    group_base=ou=groups,dc=example,dc=org
                    """
                ),
                encoding="utf-8",
            )
            app = SlurmQuotaServeApp()
            app.load_settings(self.defs_path, site_ini)
        assert app.settings is not None
        self.assertEqual(app.settings.authentication.method, "ldap")
        self.assertEqual(app.settings.ldap.uri.geturl(), "ldap://ldap.example.org")
        self.assertEqual(app.settings.ldap.user_base, "ou=people,dc=example,dc=org")

    def test_site_ini_parses_restricted_groups_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            site_ini = Path(tmp) / "serve.ini"
            site_ini.write_text(
                textwrap.dedent(
                    """\
                    [ldap]
                    restricted_groups=
                      admins
                      hpc-users
                    """
                ),
                encoding="utf-8",
            )
            app = SlurmQuotaServeApp()
            app.load_settings(self.defs_path, site_ini)
        assert app.settings is not None
        self.assertEqual(app.settings.ldap.restricted_groups, ["admins", "hpc-users"])


class TestSetup(SlurmQuotaTestCase):
    def setUp(self):
        super().setUp()
        self.defs_path = conf_defs_path()
        self._patch_slurm = patch("slurm_quota.serve.app.auth.require_slurm_user")
        self._patch_slurm.start()
        self.addCleanup(self._patch_slurm.stop)

    def test_fails_when_load_settings_fails(self):
        app = SlurmQuotaServeApp()
        with self.assertRaises(SettingsDefinitionError):
            app.setup(Path("/no/such/serve.yml"), Path("/no/such/site.ini"))
        self.assertIsNone(app.settings)

    def test_fails_when_site_config_missing(self):
        app = SlurmQuotaServeApp()
        with self.assertRaises(ServeSetupError) as cm:
            app.setup(self.defs_path, Path("/no/such/site.ini"))
        self.assertIsNotNone(app.settings)
        self.assertIn("[ldap] uri", str(cm.exception))

    def test_fails_when_validate_settings_fails(self):
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
            app = SlurmQuotaServeApp()
            with self.assertRaises(ServeSetupError) as cm:
                app.setup(self.defs_path, site_ini)
        self.assertIn("[tls] cert", str(cm.exception))

    def test_fails_when_load_cert_chain_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cert = tmp_path / "cert.pem"
            key = tmp_path / "key.pem"
            cert.write_text("not a certificate", encoding="utf-8")
            key.write_text("not a private key", encoding="utf-8")
            site_ini = write_jwt_tls_site_ini(tmp_path, cert, key)
            app = SlurmQuotaServeApp()
            with (
                patch("slurm_quota.serve.app.init_database"),
                self.assertRaises(ServeSetupError) as cm,
            ):
                app.setup(self.defs_path, site_ini)
        self.assertIn("Unable to load TLS certificate chain", str(cm.exception))
        self.assertIsNone(app.ssl_context)

    def test_sets_ssl_context_when_tls_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cert, key = write_test_tls_certs(tmp_path)
            site_ini = write_jwt_tls_site_ini(tmp_path, cert, key)
            app = SlurmQuotaServeApp()
            with patch("slurm_quota.serve.app.init_database"):
                app.setup(self.defs_path, site_ini)
        self.assertIsInstance(app.ssl_context, ssl.SSLContext)
        self.assertEqual(app.scheme, "HTTPS")

    def test_rejects_non_slurm_user(self):
        self._patch_slurm.stop()
        with tempfile.TemporaryDirectory() as tmp:
            site_ini = write_jwt_site_ini(Path(tmp))
            with patch(
                "slurm_quota.serve.app.auth.get_current_user", return_value="root"
            ):
                app = SlurmQuotaServeApp()
                with self.assertRaises(ServeSetupError) as cm:
                    app.setup(self.defs_path, site_ini)
        self.assertIn("Must be run as slurm user", str(cm.exception))

    def test_creates_database_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            site_ini = write_jwt_site_ini(Path(tmp))
            self.assertFalse(os.path.exists(self.db_path))
            app = SlurmQuotaServeApp()
            app.setup(self.defs_path, site_ini)
            self.assertTrue(os.path.exists(self.db_path))

    @patch("slurm_quota.serve.app.LDAPAuthentifier")
    def test_sets_jwt_and_authentifier_for_ldap_method(self, m_ldap_cls):
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
                    duration=2
                    """
                ),
                encoding="utf-8",
            )
            init_database()
            app = SlurmQuotaServeApp()
            app.setup(self.defs_path, site_ini)

        self.assertIsNotNone(app.authentifier)
        m_ldap_cls.assert_called_once()
        user = AuthenticatedUser(login="alice", groups=["users"])
        token = app.jwt.generate(user, 2)
        decoded = app.jwt.decode(token)
        self.assertEqual(decoded.login, "alice")

    def test_sets_jwt_without_authentifier_for_jwt_method(self):
        with tempfile.TemporaryDirectory() as tmp:
            site_ini = write_jwt_site_ini(Path(tmp))
            init_database()
            app = SlurmQuotaServeApp()
            app.setup(self.defs_path, site_ini)

        self.assertIsNone(app.authentifier)
        user = AuthenticatedUser(login="alice", groups=[])
        token = app.jwt.generate(user, app.settings.jwt.duration)
        decoded = app.jwt.decode(token)
        self.assertEqual(decoded.login, "alice")


class TestScheme(SlurmQuotaTestCase):
    def setUp(self):
        super().setUp()
        self.defs_path = conf_defs_path()
        self._patch_slurm = patch("slurm_quota.serve.app.auth.require_slurm_user")
        self._patch_slurm.start()
        self.addCleanup(self._patch_slurm.stop)

    def test_http_when_ssl_context_unset(self):
        app = SlurmQuotaServeApp()
        self.assertEqual(app.scheme, "HTTP")

    def test_http_after_setup_without_tls(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = SlurmQuotaServeApp()
            with patch("slurm_quota.serve.app.init_database"):
                app.setup(self.defs_path, write_jwt_site_ini(Path(tmp)))
        self.assertIsNone(app.ssl_context)
        self.assertEqual(app.scheme, "HTTP")

    def test_https_after_setup_with_tls(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cert, key = write_test_tls_certs(tmp_path)
            site_ini = write_jwt_tls_site_ini(tmp_path, cert, key)
            app = SlurmQuotaServeApp()
            with patch("slurm_quota.serve.app.init_database"):
                app.setup(self.defs_path, site_ini)
        self.assertIsNotNone(app.ssl_context)
        self.assertEqual(app.scheme, "HTTPS")
