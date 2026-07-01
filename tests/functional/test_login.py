"""Functional tests: `login` subcommand."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from slurm_quota.token import service_token_path
from tests.functional.functional_base import FakeJsonUrlopenResponse, FunctionalCLIBase
from tests.testing_utils import fake_http_error


class TestLoginCommand(FunctionalCLIBase):
    def _login_urlopen_side_effect(self, request, **_kwargs):
        if request.full_url.endswith("/login"):
            return FakeJsonUrlopenResponse({"token": "jwt-token"})
        raise AssertionError(f"unexpected request: {request.full_url}")

    def test_login_prints_token_by_default(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env({"XDG_CONFIG_HOME": str(config_home)})
        with (
            patch(
                "slurm_quota.client.urlopen",
                side_effect=self._login_urlopen_side_effect,
            ),
            patch("slurm_quota.commands.getpass.getpass", return_value="secret"),
            patch("slurm_quota.auth.get_current_user", return_value="alice"),
            self.capture_stdout() as out,
        ):
            self.run_cli_main(["slurm-quota", "login"])
        self.assertEqual(out.getvalue(), "jwt-token\n")
        self.assertFalse(service_token_path().exists())

    def test_login_save_writes_token_and_prints_confirmation(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env({"XDG_CONFIG_HOME": str(config_home)})
        with (
            patch(
                "slurm_quota.client.urlopen",
                side_effect=self._login_urlopen_side_effect,
            ),
            patch("slurm_quota.commands.getpass.getpass", return_value="secret"),
            patch("slurm_quota.auth.get_current_user", return_value="alice"),
            self.capture_stdout() as out,
        ):
            self.run_cli_main(["slurm-quota", "login", "--save"])
        token_path = service_token_path()
        self.assertEqual(token_path.read_text(encoding="utf-8"), "jwt-token")
        self.assertEqual(
            out.getvalue(),
            f"Authentication token saved to {token_path}\n",
        )

    def test_login_uses_explicit_username(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env({"XDG_CONFIG_HOME": str(config_home)})
        with (
            patch(
                "slurm_quota.client.urlopen",
                side_effect=self._login_urlopen_side_effect,
            ) as m_urlopen,
            patch("slurm_quota.commands.getpass.getpass", return_value="secret"),
            self.capture_stdout(),
        ):
            self.run_cli_main(["slurm-quota", "login", "bob"])
        req = m_urlopen.call_args[0][0]
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["username"], "bob")

    def test_login_rejects_invalid_credentials(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env({"XDG_CONFIG_HOME": str(config_home)})

        def _unauthorized(request, **_kwargs):
            raise fake_http_error(401, url=request.full_url)

        with (
            patch("slurm_quota.client.urlopen", side_effect=_unauthorized),
            patch("slurm_quota.commands.getpass.getpass", return_value="wrong"),
            patch("slurm_quota.auth.get_current_user", return_value="alice"),
        ):
            self.run_cli_main_exit(["slurm-quota", "login"], 1)
        self.assertFalse(service_token_path().exists())

    def test_login_reports_auth_disabled(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env({"XDG_CONFIG_HOME": str(config_home)})

        def _not_found(request, **_kwargs):
            raise fake_http_error(404, {"error": "not_found"}, url=request.full_url)

        with (
            patch("slurm_quota.client.urlopen", side_effect=_not_found),
            patch("slurm_quota.commands.getpass.getpass", return_value="secret"),
            patch("slurm_quota.auth.get_current_user", return_value="alice"),
        ):
            self.run_cli_main_exit(["slurm-quota", "login"], 1)
        self.assertFalse(service_token_path().exists())
