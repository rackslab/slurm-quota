"""Functional tests: `slurm-quota token` subcommand."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from slurm_quota.token import ClientToken, TokenPayload
from tests.functional.functional_base import FunctionalCLIBase
from tests.testing_utils import craft_jwt, dedent_lines


class TestTokenCommand(FunctionalCLIBase):
    def test_token_show_prints_metadata_from_env_token(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        token = craft_jwt(login="alice", exp=4_102_444_800)
        self.env(
            {
                "XDG_CONFIG_HOME": str(config_home),
                "SLURM_QUOTA_TOKEN": token,
            }
        )
        with (
            patch.object(
                TokenPayload,
                "expiry",
                return_value="2099-12-31 23:59:59 UTC",
            ),
            self.capture_stdout() as out,
        ):
            self.run_cli_main(["slurm-quota", "token"])
        self.assertEqual(
            out.getvalue(),
            dedent_lines(
                "Source: SLURM_QUOTA_TOKEN (environment)",
                "Username: alice",
                "Expires: 2099-12-31 23:59:59 UTC",
            ),
        )

    def test_token_show_prints_metadata_from_saved_file(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env({"XDG_CONFIG_HOME": str(config_home)})
        token = craft_jwt(login="bob", exp=4_102_444_800)
        ClientToken(token, "").save()
        with (
            patch.object(
                TokenPayload,
                "expiry",
                return_value="2099-12-31 23:59:59 UTC",
            ),
            self.capture_stdout() as out,
        ):
            self.run_cli_main(["slurm-quota", "token"])
        self.assertEqual(
            out.getvalue(),
            dedent_lines(
                f"Source: {ClientToken.path()}",
                "Username: bob",
                "Expires: 2099-12-31 23:59:59 UTC",
            ),
        )

    def test_token_show_exits_when_no_token(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env({"XDG_CONFIG_HOME": str(config_home)})
        self.run_cli_main_exit(["slurm-quota", "token"], 1)

    def test_token_save_writes_env_token(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env(
            {
                "XDG_CONFIG_HOME": str(config_home),
                "SLURM_QUOTA_TOKEN": "env-jwt-token",
            }
        )
        with self.capture_stdout() as out:
            self.run_cli_main(["slurm-quota", "token", "--save"])
        token_path = ClientToken.path()
        self.assertEqual(token_path.read_text(encoding="utf-8"), "env-jwt-token")
        self.assertEqual(
            out.getvalue(),
            f"Authentication token saved to {token_path}\n",
        )

    def test_token_save_requires_env_token(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env({"XDG_CONFIG_HOME": str(config_home)})
        with patch.dict("os.environ", {"SLURM_QUOTA_TOKEN": ""}, clear=False):
            self.run_cli_main_exit(["slurm-quota", "token", "--save"], 1)
        self.assertFalse(ClientToken.path().exists())
