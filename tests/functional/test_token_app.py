"""Functional tests: `token create`, `token list` and `token revoke` subcommands."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from tests.functional.functional_base import (
    FakeJsonUrlopenResponse,
    FakeNoContentUrlopenResponse,
    FunctionalAPICliBase,
)
from tests.testing_utils import fake_http_error

_TOKEN = {
    "id": "0123abcd",
    "username": "bob",
    "name": "ci",
    "scopes": ["stats:read:own"],
    "created_at": 1_700_000_000,
    "expires_at": 1_707_776_000,
    "revoked_at": None,
    "status": "active",
}


class _CreatedUrlopenResponse(FakeJsonUrlopenResponse):
    def __init__(self, payload):
        super().__init__(payload)
        self.status = 201


class TestTokenAppCommands(FunctionalAPICliBase):
    def test_token_create_prints_token_on_stdout(self):
        stderr = io.StringIO()
        with (
            patch(
                "slurm_quota.client.urlopen",
                return_value=_CreatedUrlopenResponse({**_TOKEN, "token": "app-jwt"}),
            ) as m_urlopen,
            self.capture_stdout() as out,
            redirect_stderr(stderr),
        ):
            self.run_cli_main(
                [
                    "slurm-quota",
                    "token",
                    "create",
                    "ci",
                    "--scope",
                    "stats:read:own",
                    "--duration",
                    "90",
                ]
            )
        self.assertEqual(out.getvalue(), "app-jwt\n")
        self.assertIn(
            "Application token ci created with ID 0123abcd", stderr.getvalue()
        )
        self.assertIn("cannot be displayed again", stderr.getvalue())
        req = m_urlopen.call_args[0][0]
        self.assertTrue(req.full_url.endswith("/tokens"))
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Authorization"), "Bearer saved-jwt")
        self.assertEqual(
            json.loads(req.data.decode("utf-8")),
            {"name": "ci", "scopes": ["stats:read:own"], "duration": 90},
        )

    def test_token_create_requires_scope(self):
        with redirect_stderr(io.StringIO()):
            self.run_cli_main_exit(["slurm-quota", "token", "create", "ci"], code=2)

    def test_token_create_rejects_unknown_scope(self):
        with redirect_stderr(io.StringIO()):
            self.run_cli_main_exit(
                ["slurm-quota", "token", "create", "ci", "--scope", "foo"], code=2
            )

    def test_token_create_reports_forbidden_scope(self):
        with (
            patch(
                "slurm_quota.client.urlopen",
                side_effect=fake_http_error(
                    403, {"message": "Scopes not allowed for user: quotas:write"}
                ),
            ),
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
        ):
            self.run_cli_main_exit(
                ["slurm-quota", "token", "create", "ci", "--scope", "quotas:write"]
            )
        self.assertIn(
            "Access denied: Scopes not allowed for user: quotas:write",
            log_cm.output[0],
        )

    def test_token_list_prints_table(self):
        with (
            patch(
                "slurm_quota.client.urlopen",
                return_value=FakeJsonUrlopenResponse({"tokens": [_TOKEN]}),
            ),
            self.capture_stdout() as out,
        ):
            self.run_cli_main(["slurm-quota", "token", "list"])
        lines = out.getvalue().splitlines()
        self.assertEqual(
            lines[0].split(), ["ID", "NAME", "SCOPES", "CREATED", "EXPIRES", "STATUS"]
        )
        self.assertTrue(lines[1].startswith("0123abcd  ci"))
        self.assertTrue(lines[1].endswith("active"))

    def test_token_list_all_users(self):
        with (
            patch(
                "slurm_quota.client.urlopen",
                return_value=FakeJsonUrlopenResponse({"tokens": [_TOKEN]}),
            ) as m_urlopen,
            self.capture_stdout() as out,
        ):
            self.run_cli_main(["slurm-quota", "token", "list", "--all"])
        self.assertIn("USER", out.getvalue().splitlines()[0])
        query = parse_qs(urlparse(m_urlopen.call_args[0][0].full_url).query)
        self.assertEqual(query, {"all": ["true"]})

    def test_token_list_empty(self):
        with (
            patch(
                "slurm_quota.client.urlopen",
                return_value=FakeJsonUrlopenResponse({"tokens": []}),
            ),
            self.capture_stdout() as out,
        ):
            self.run_cli_main(["slurm-quota", "token", "list"])
        self.assertEqual(out.getvalue(), "No application tokens found\n")

    def test_token_revoke(self):
        with (
            patch(
                "slurm_quota.client.urlopen",
                return_value=FakeNoContentUrlopenResponse(),
            ) as m_urlopen,
            self.capture_stdout() as out,
        ):
            self.run_cli_main(["slurm-quota", "token", "revoke", "0123abcd"])
        self.assertEqual(out.getvalue(), "Revoked application token 0123abcd\n")
        req = m_urlopen.call_args[0][0]
        self.assertTrue(req.full_url.endswith("/tokens/0123abcd"))
        self.assertEqual(req.get_method(), "DELETE")

    def test_token_revoke_not_found(self):
        with (
            patch(
                "slurm_quota.client.urlopen",
                side_effect=fake_http_error(404, {"message": "Token not found"}),
            ),
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
        ):
            self.run_cli_main_exit(["slurm-quota", "token", "revoke", "missing"])
        self.assertIn("Application token missing not found", log_cm.output[0])
