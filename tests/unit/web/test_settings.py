"""Unit tests for slurm_quota.web.settings."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from slurm_quota.web.settings import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_SESSION_DAYS,
    SlurmQuotaWebSettings,
    load_env_file,
)
from tests.test_support import SlurmQuotaTestCase


def _module_file(tmp: Path, *, under_user_site: Path | None = None) -> Path:
    if under_user_site is not None:
        return under_user_site / "slurm_quota" / "web" / "settings.py"
    return tmp / "src" / "slurm_quota" / "web" / "settings.py"


class TestLoadEnvFile(SlurmQuotaTestCase):
    def test_load_env_file(self):
        env_file = Path(self._tmp.name) / "defaults"
        env_file.write_text(
            "SLURM_QUOTA_URL=http://from-file:9911/\n"
            "# comment\n"
            'SLURM_QUOTA_WEB_DEBUG="1"\n'
        )
        missing = Path(self._tmp.name) / "missing"

        with patch.dict("os.environ", {}, clear=True):
            load_env_file(env_file)
            self.assertEqual(os.environ["SLURM_QUOTA_URL"], "http://from-file:9911/")
            self.assertEqual(os.environ["SLURM_QUOTA_WEB_DEBUG"], "1")

        with patch.dict(
            "os.environ",
            {"SLURM_QUOTA_URL": "http://existing:9911/"},
            clear=True,
        ):
            load_env_file(env_file)
            self.assertEqual(os.environ["SLURM_QUOTA_URL"], "http://existing:9911/")

        with patch.dict("os.environ", {}, clear=True):
            load_env_file(missing)
            self.assertNotIn("SLURM_QUOTA_URL", os.environ)


class TestSlurmQuotaWebSettings(SlurmQuotaTestCase):
    def test_from_os_environ(self):
        with patch.dict(
            "os.environ",
            {
                "SLURM_QUOTA_URL": "http://controller:9911/",
                "SLURM_QUOTA_WEB_HOST": "0.0.0.0",
                "SLURM_QUOTA_WEB_SECURE_COOKIES": "yes",
                "SLURM_QUOTA_WEB_SESSION_DAYS": "7",
                "SLURM_QUOTA_WEB_PORT": "8080",
                "SLURM_QUOTA_WEB_DEBUG": "1",
            },
            clear=True,
        ):
            settings = SlurmQuotaWebSettings.from_os_environ()
        self.assertEqual(settings.quota_url, "http://controller:9911/")
        self.assertEqual(settings.host, "0.0.0.0")
        self.assertIsNone(settings.token)
        self.assertIsNone(settings.session_key_file)
        self.assertTrue(settings.secure_cookies)
        self.assertEqual(settings.session_days, 7)
        self.assertEqual(settings.port, 8080)
        self.assertTrue(settings.debug)

        with patch.dict("os.environ", {}, clear=True):
            settings = SlurmQuotaWebSettings.from_os_environ()
        self.assertEqual(settings.host, DEFAULT_HOST)
        self.assertEqual(settings.port, DEFAULT_PORT)
        self.assertEqual(settings.session_days, DEFAULT_SESSION_DAYS)
        self.assertFalse(settings.secure_cookies)
        self.assertFalse(settings.debug)

        with patch.dict(
            "os.environ",
            {
                "SLURM_QUOTA_WEB_PORT": "not-a-port",
                "SLURM_QUOTA_WEB_SESSION_DAYS": "0",
            },
            clear=True,
        ):
            settings = SlurmQuotaWebSettings.from_os_environ()
        self.assertEqual(settings.port, DEFAULT_PORT)
        self.assertEqual(settings.session_days, DEFAULT_SESSION_DAYS)

        env_file = Path(self._tmp.name) / "defaults"
        env_file.write_text(
            "SLURM_QUOTA_URL=http://once:9911/\n"
            "SLURM_QUOTA_WEB_SESSION_KEY_FILE=/etc/key\n"
        )
        with patch.dict("os.environ", {}, clear=True):
            settings = SlurmQuotaWebSettings.from_os_environ()
            self.assertIsNone(settings.quota_url)

            load_env_file(env_file)
            settings = SlurmQuotaWebSettings.from_os_environ()
            self.assertEqual(settings.quota_url, "http://once:9911/")
            self.assertEqual(settings.session_key_file, Path("/etc/key"))


class TestLoadSessionKey(SlurmQuotaTestCase):
    def test_load_session_key_from_env(self):
        with patch.dict("os.environ", {"SLURM_QUOTA_WEB_SESSION_KEY": "from-env"}):
            settings = SlurmQuotaWebSettings.from_os_environ()
            self.assertEqual(settings.load_session_key(), "from-env")

    def test_load_session_key_from_file(self):
        key_file = Path(self._tmp.name) / "web-session.key"
        key_file.write_text("from-file\n")
        with patch.dict(
            "os.environ",
            {"SLURM_QUOTA_WEB_SESSION_KEY_FILE": str(key_file)},
            clear=True,
        ):
            settings = SlurmQuotaWebSettings.from_os_environ()
            self.assertEqual(settings.load_session_key(), "from-file")

    def test_load_session_key_prefers_env_over_file(self):
        key_file = Path(self._tmp.name) / "web-session.key"
        key_file.write_text("from-file\n")
        with patch.dict(
            "os.environ",
            {
                "SLURM_QUOTA_WEB_SESSION_KEY": "from-env",
                "SLURM_QUOTA_WEB_SESSION_KEY_FILE": str(key_file),
            },
        ):
            settings = SlurmQuotaWebSettings.from_os_environ()
            self.assertEqual(settings.load_session_key(), "from-env")


class TestAssetsRoot(SlurmQuotaTestCase):
    def test_env_override(self):
        custom = Path(self._tmp.name) / "custom-assets"
        custom.mkdir()
        with patch.dict("os.environ", {"SLURM_QUOTA_WEB_ASSETS_DIR": str(custom)}):
            settings = SlurmQuotaWebSettings.from_os_environ()
            self.assertEqual(settings.assets_root(), custom)

    def test_repo_checkout(self):
        tmp = Path(self._tmp.name)
        module_file = _module_file(tmp)
        module_file.parent.mkdir(parents=True)
        repo_web = tmp / "web"
        repo_web.mkdir()
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("slurm_quota.web.settings.__file__", str(module_file)),
        ):
            settings = SlurmQuotaWebSettings.from_os_environ()
            self.assertEqual(settings.assets_root(), repo_web)

    def test_install_prefix_data_directory(self):
        tmp = Path(self._tmp.name)
        module_file = _module_file(tmp)
        module_file.parent.mkdir(parents=True)
        data_root = tmp / "prefix" / "slurm-quota" / "web"
        (data_root / "templates").mkdir(parents=True)
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("slurm_quota.web.settings.__file__", str(module_file)),
            patch(
                "slurm_quota.web.settings.sysconfig.get_path",
                return_value=str(tmp / "prefix"),
            ),
        ):
            settings = SlurmQuotaWebSettings.from_os_environ()
            self.assertEqual(settings.assets_root(), data_root)

    def test_user_site_install(self):
        tmp = Path(self._tmp.name)
        user_site = tmp / "site-packages"
        module_file = _module_file(tmp, under_user_site=user_site)
        module_file.parent.mkdir(parents=True)
        user_data = tmp / "userbase" / "slurm-quota" / "web"
        (user_data / "templates").mkdir(parents=True)
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("slurm_quota.web.settings.__file__", str(module_file)),
            patch(
                "slurm_quota.web.settings.sysconfig.get_path",
                return_value=str(tmp / "prefix"),
            ),
            patch(
                "slurm_quota.web.settings.site.getusersitepackages",
                return_value=str(user_site),
            ),
            patch(
                "slurm_quota.web.settings.site.getuserbase",
                return_value=str(tmp / "userbase"),
            ),
        ):
            settings = SlurmQuotaWebSettings.from_os_environ()
            self.assertEqual(settings.assets_root(), user_data)

    def test_fallback_to_usr_share(self):
        tmp = Path(self._tmp.name)
        module_file = _module_file(tmp)
        module_file.parent.mkdir(parents=True)
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("slurm_quota.web.settings.__file__", str(module_file)),
            patch(
                "slurm_quota.web.settings.sysconfig.get_path",
                return_value=str(tmp / "prefix"),
            ),
            patch(
                "slurm_quota.web.settings.site.getusersitepackages",
                return_value="",
            ),
        ):
            settings = SlurmQuotaWebSettings.from_os_environ()
            self.assertEqual(settings.assets_root(), Path("/usr/share/slurm-quota/web"))

    def test_env_override_takes_precedence_over_repo(self):
        tmp = Path(self._tmp.name)
        module_file = _module_file(tmp)
        module_file.parent.mkdir(parents=True)
        (tmp / "web").mkdir()
        custom = tmp / "custom-assets"
        custom.mkdir()
        with (
            patch.dict("os.environ", {"SLURM_QUOTA_WEB_ASSETS_DIR": str(custom)}),
            patch("slurm_quota.web.settings.__file__", str(module_file)),
        ):
            settings = SlurmQuotaWebSettings.from_os_environ()
            self.assertEqual(settings.assets_root(), custom)
