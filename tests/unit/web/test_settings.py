"""Unit tests for slurm_quota.web.settings."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from slurm_quota.web.settings import assets_root, load_session_key

from tests.test_support import SlurmQuotaTestCase


def _module_file(tmp: Path, *, under_user_site: Path | None = None) -> Path:
    if under_user_site is not None:
        return under_user_site / "slurm_quota" / "web" / "settings.py"
    return tmp / "src" / "slurm_quota" / "web" / "settings.py"


class TestAssetsRoot(SlurmQuotaTestCase):
    def test_env_override(self):
        custom = Path(self._tmp.name) / "custom-assets"
        custom.mkdir()
        with patch.dict("os.environ", {"SLURM_QUOTA_WEB_ASSETS_DIR": str(custom)}):
            self.assertEqual(assets_root(), custom)

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
            self.assertEqual(assets_root(), repo_web)

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
            self.assertEqual(assets_root(), data_root)

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
            self.assertEqual(assets_root(), user_data)

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
            self.assertEqual(assets_root(), Path("/usr/share/slurm-quota/web"))

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
            self.assertEqual(assets_root(), custom)


class TestLoadSessionKey(SlurmQuotaTestCase):
    def test_load_session_key_from_env(self):
        with patch.dict("os.environ", {"SLURM_QUOTA_WEB_SESSION_KEY": "from-env"}):
            self.assertEqual(load_session_key(), "from-env")

    def test_load_session_key_from_file(self):
        key_file = Path(self._tmp.name) / "web-session.key"
        key_file.write_text("from-file\n")
        with patch.dict(
            "os.environ",
            {"SLURM_QUOTA_WEB_SESSION_KEY_FILE": str(key_file)},
            clear=True,
        ):
            self.assertEqual(load_session_key(), "from-file")

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
            self.assertEqual(load_session_key(), "from-env")
