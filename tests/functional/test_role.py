"""Functional tests: `role` subcommand (show, list, grant, revoke)."""

from __future__ import annotations

import json
from unittest.mock import patch
from urllib.error import URLError

from slurm_quota.token import service_token_path

from tests.functional.functional_base import (
    FakeJsonUrlopenResponse,
    FunctionalAPICliBase,
)
from tests.testing_utils import dedent_lines


class _RoleUrlopenResponse:
    def __init__(self, payload=None, *, status: int = 200):
        self.status = status
        self._payload = payload if payload is not None else {}

    def read(self):
        if self.status == 204:
            return b""
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class TestRoleCommand(FunctionalAPICliBase):
    def _role_urlopen_side_effect(self, request):
        url = request.full_url
        method = request.get_method()
        if url.endswith("/me"):
            return FakeJsonUrlopenResponse({"username": "alice", "role": "admin"})
        if url.endswith("/roles") and method == "GET":
            return FakeJsonUrlopenResponse(
                {
                    "users": [
                        {"username": "alice", "role": "admin"},
                        {"username": "bob", "role": "user"},
                    ]
                }
            )
        if "/roles/operators/" in url and method == "PUT":
            return _RoleUrlopenResponse(status=204)
        if "/roles/operators/" in url and method == "DELETE":
            return _RoleUrlopenResponse(status=204)
        if "/roles/managers/" in url and method == "PUT":
            return _RoleUrlopenResponse(status=204)
        if "/roles/managers/" in url and method == "DELETE":
            return _RoleUrlopenResponse(status=204)
        raise AssertionError(f"unexpected request: {method} {url}")

    def test_role_show_prints_username_and_role(self):
        with (
            patch(
                "slurm_quota.client.urlopen",
                side_effect=self._role_urlopen_side_effect,
            ),
            self.capture_stdout() as out,
        ):
            self.run_cli_main(["slurm-quota", "role", "show"])
        self.assertEqual(
            out.getvalue(),
            dedent_lines(
                "Username: alice",
                "Role: admin",
            ),
        )

    def test_role_list_prints_users_table(self):
        with (
            patch(
                "slurm_quota.client.urlopen",
                side_effect=self._role_urlopen_side_effect,
            ),
            self.capture_stdout() as out,
        ):
            self.run_cli_main(["slurm-quota", "role", "list"])
        self.assertEqual(
            out.getvalue(),
            dedent_lines(
                "USERNAME  ROLE ",
                "alice     admin",
                "bob       user ",
            ),
        )

    def test_role_list_prints_message_when_empty(self):
        def _empty_roles(request):
            if request.full_url.endswith("/roles"):
                return FakeJsonUrlopenResponse({"users": []})
            raise AssertionError(f"unexpected request: {request.full_url}")

        with (
            patch("slurm_quota.client.urlopen", side_effect=_empty_roles),
            self.capture_stdout() as out,
        ):
            self.run_cli_main(["slurm-quota", "role", "list"])
        self.assertEqual(out.getvalue(), "No users found\n")

    def test_role_grant_operator_prints_confirmation(self):
        with (
            patch(
                "slurm_quota.client.urlopen",
                side_effect=self._role_urlopen_side_effect,
            ) as m_urlopen,
            self.capture_stdout() as out,
        ):
            self.run_cli_main(["slurm-quota", "role", "grant", "operator", "bob"])
        self.assertEqual(out.getvalue(), "Granted operator role to bob\n")
        req = m_urlopen.call_args[0][0]
        self.assertEqual(req.get_method(), "PUT")
        self.assertTrue(req.full_url.endswith("/roles/operators/bob"))
        self.assertEqual(req.get_header("Authorization"), "Bearer saved-jwt")

    def test_role_revoke_manager_prints_confirmation(self):
        with (
            patch(
                "slurm_quota.client.urlopen",
                side_effect=self._role_urlopen_side_effect,
            ) as m_urlopen,
            self.capture_stdout() as out,
        ):
            self.run_cli_main(["slurm-quota", "role", "revoke", "manager", "bob"])
        self.assertEqual(out.getvalue(), "Revoked manager role from bob\n")
        req = m_urlopen.call_args[0][0]
        self.assertEqual(req.get_method(), "DELETE")
        self.assertTrue(req.full_url.endswith("/roles/managers/bob"))
        self.assertEqual(req.get_header("Authorization"), "Bearer saved-jwt")

    def test_role_without_subcommand_exits_with_help(self):
        self.run_cli_main_exit(["slurm-quota", "role"], 1)

    def test_role_commands_require_token(self):
        service_token_path().unlink()
        with self.assertLogs("slurm_quota", level="ERROR") as log_cm:
            self.run_cli_main_exit(["slurm-quota", "role", "show"], 1)
        self.assertIn("No API token available", log_cm.output[0])

    def test_role_show_reports_access_denied_on_forbidden(self):
        def _forbidden(request):
            response = FakeJsonUrlopenResponse({})
            response.status = 403
            return response

        with (
            patch("slurm_quota.client.urlopen", side_effect=_forbidden),
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
        ):
            self.run_cli_main_exit(["slurm-quota", "role", "show"], 1)
        self.assertEqual(log_cm.output, ["ERROR:slurm_quota:Access denied"])

    def test_role_list_reports_admin_required_on_forbidden(self):
        def _forbidden(request):
            response = FakeJsonUrlopenResponse({})
            response.status = 403
            return response

        with (
            patch("slurm_quota.client.urlopen", side_effect=_forbidden),
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
        ):
            self.run_cli_main_exit(["slurm-quota", "role", "list"], 1)
        self.assertEqual(
            log_cm.output,
            ["ERROR:slurm_quota:Access denied: admin role required to list roles"],
        )

    def test_role_grant_reports_admin_required_on_forbidden(self):
        def _forbidden(request):
            response = _RoleUrlopenResponse(status=403)
            return response

        with (
            patch("slurm_quota.client.urlopen", side_effect=_forbidden),
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
        ):
            self.run_cli_main_exit(
                ["slurm-quota", "role", "grant", "operator", "bob"], 1
            )
        self.assertEqual(
            log_cm.output,
            ["ERROR:slurm_quota:Access denied: admin role required to grant operator"],
        )

    def test_role_revoke_reports_admin_required_on_forbidden(self):
        def _forbidden(request):
            response = _RoleUrlopenResponse(status=403)
            return response

        with (
            patch("slurm_quota.client.urlopen", side_effect=_forbidden),
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
        ):
            self.run_cli_main_exit(
                ["slurm-quota", "role", "revoke", "manager", "bob"], 1
            )
        self.assertEqual(
            log_cm.output,
            ["ERROR:slurm_quota:Access denied: admin role required to revoke manager"],
        )

    def test_role_show_reports_unreachable_service(self):
        with (
            patch("slurm_quota.client.urlopen", side_effect=URLError("boom")),
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
        ):
            self.run_cli_main_exit(["slurm-quota", "role", "show"], 1)
        self.assertEqual(len(log_cm.output), 1)
        self.assertIn("Failed to contact slurm-quota service:", log_cm.output[0])
        self.assertIn("boom", log_cm.output[0])
