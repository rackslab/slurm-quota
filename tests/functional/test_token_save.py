"""Functional tests: `slurm-quota token` subcommand."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from slurm_quota.token import service_token_path
from tests.functional.functional_base import FunctionalCLIBase


class TestTokenSaveCommand(FunctionalCLIBase):
    def test_token_save_writes_env_token(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env(
            {
                "XDG_CONFIG_HOME": str(config_home),
                "SLURM_QUOTA_TOKEN": "env-jwt-token",
            }
        )
        with self.capture_stdout() as out:
            self.run_cli_main(["slurm-quota", "token"])
        token_path = service_token_path()
        self.assertEqual(token_path.read_text(encoding="utf-8"), "env-jwt-token")
        self.assertEqual(
            out.getvalue(),
            f"Authentication token saved to {token_path}\n",
        )

    def test_token_save_requires_env_token(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env({"XDG_CONFIG_HOME": str(config_home)})
        with patch.dict("os.environ", {"SLURM_QUOTA_TOKEN": ""}, clear=False):
            self.run_cli_main_exit(["slurm-quota", "token"], 1)
        self.assertFalse(service_token_path().exists())
