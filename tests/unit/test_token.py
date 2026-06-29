"""Unit tests for slurm_quota.token."""

from __future__ import annotations

import stat
from pathlib import Path

from slurm_quota.token import (
    load_service_token,
    save_service_token,
    service_token_path,
)
from tests.test_support import SlurmQuotaTestCase


class TestTokenStore(SlurmQuotaTestCase):
    def test_service_token_path_uses_xdg_config_home(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env({"XDG_CONFIG_HOME": str(config_home)})
        self.assertEqual(
            service_token_path(),
            config_home / "slurm-quota" / "token",
        )

    def test_save_service_token_creates_restricted_file(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env({"XDG_CONFIG_HOME": str(config_home)})
        path = save_service_token("eyJ.test.token\n")
        self.assertEqual(path.read_text(encoding="utf-8"), "eyJ.test.token")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(
            stat.S_IMODE(path.parent.stat().st_mode) & 0o777,
            0o700,
        )

    def test_load_service_token_reads_saved_file(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env({"XDG_CONFIG_HOME": str(config_home)})
        save_service_token("saved-token")
        self.assertEqual(load_service_token(), "saved-token")

    def test_load_service_token_prefers_env_override(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env(
            {
                "XDG_CONFIG_HOME": str(config_home),
                "SLURM_QUOTA_TOKEN": "env-token",
            }
        )
        save_service_token("file-token")
        self.assertEqual(load_service_token(), "env-token")

    def test_load_service_token_returns_none_when_missing(self):
        config_home = Path(self._tmp.name) / "empty-config"
        self.env({"XDG_CONFIG_HOME": str(config_home)})
        self.assertIsNone(load_service_token())

    def test_load_service_token_ignores_empty_env_override(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env(
            {
                "XDG_CONFIG_HOME": str(config_home),
                "SLURM_QUOTA_TOKEN": "   ",
            }
        )
        save_service_token("file-token")
        self.assertEqual(load_service_token(), "file-token")
