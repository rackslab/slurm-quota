"""Functional tests: `adjust` subcommand."""

from __future__ import annotations

import json
from unittest.mock import patch
from urllib.error import URLError

from slurm_quota.token import service_token_path
from tests.functional.functional_base import (
    FakeJsonUrlopenResponse,
    FakeNoContentUrlopenResponse,
    FunctionalAPICliBase,
)


class TestAdjustCommand(FunctionalAPICliBase):
    def _adjust_urlopen_side_effect(self, request):
        url = request.full_url
        method = request.get_method()
        if method == "PATCH" and "/consumption/user/" in url and url.endswith("/cpu"):
            body = json.loads(request.data.decode("utf-8"))
            self.assertIn("delta_minutes", body)
            return FakeJsonUrlopenResponse({"total_consumed_minutes": 130})
        if (
            method == "PATCH"
            and "/consumption/account/" in url
            and url.endswith("/gpu")
        ):
            body = json.loads(request.data.decode("utf-8"))
            self.assertIn("delta_minutes", body)
            return FakeJsonUrlopenResponse({"total_consumed_minutes": 210})
        if method == "PATCH" and "/consumption/user/" in url and url.endswith("/gpu"):
            return FakeJsonUrlopenResponse({"total_consumed_minutes": 0})
        raise AssertionError(f"unexpected request: {method} {url}")

    def test_adjust_user_cpu_minutes(self):
        with (
            patch(
                "slurm_quota.client.urlopen",
                side_effect=self._adjust_urlopen_side_effect,
            ) as m_urlopen,
            self.capture_stdout() as out,
        ):
            self.run_cli_main(
                [
                    "slurm-quota",
                    "adjust",
                    "--user",
                    "alice",
                    "--cpu",
                    "--minutes",
                    "+30",
                ]
            )
        self.assertEqual(
            out.getvalue(),
            (
                "Successfully adjusted CPU consumed time for user alice: "
                "+30 minutes (new total: 130 minutes)\n"
            ),
        )
        req = m_urlopen.call_args[0][0]
        self.assertEqual(req.get_method(), "PATCH")
        self.assertTrue(req.full_url.endswith("/consumption/user/alice/cpu"))
        self.assertEqual(req.get_header("Authorization"), "Bearer saved-jwt")
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body, {"delta_minutes": 30})

    def test_adjust_account_gpu_hours(self):
        with (
            patch(
                "slurm_quota.client.urlopen",
                side_effect=self._adjust_urlopen_side_effect,
            ) as m_urlopen,
            self.capture_stdout() as out,
        ):
            self.run_cli_main(
                [
                    "slurm-quota",
                    "adjust",
                    "--account",
                    "hpc",
                    "--gpu",
                    "--hours",
                    "+2",
                ]
            )
        self.assertEqual(
            out.getvalue(),
            (
                "Successfully adjusted GPU consumed time for account hpc: "
                "+2 hours (new total: 210 minutes)\n"
            ),
        )
        req = m_urlopen.call_args[0][0]
        self.assertTrue(req.full_url.endswith("/consumption/account/hpc/gpu"))
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body, {"delta_minutes": 120})

    def test_adjust_requires_token(self):
        service_token_path().unlink()
        with self.assertLogs("slurm_quota", level="ERROR") as log_cm:
            self.run_cli_main_exit(
                [
                    "slurm-quota",
                    "adjust",
                    "--user",
                    "alice",
                    "--cpu",
                    "--minutes",
                    "+10",
                ],
                1,
            )
        self.assertIn("No API token available", log_cm.output[0])

    def test_adjust_reports_access_denied_on_forbidden(self):
        def _forbidden(request):
            return FakeNoContentUrlopenResponse(status=403)

        with (
            patch("slurm_quota.client.urlopen", side_effect=_forbidden),
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
        ):
            self.run_cli_main_exit(
                [
                    "slurm-quota",
                    "adjust",
                    "--user",
                    "alice",
                    "--cpu",
                    "--minutes",
                    "+10",
                ],
                1,
            )
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:Access denied: operator or admin role required "
                "to adjust consumption",
            ],
        )

    def test_adjust_reports_unreachable_service(self):
        with (
            patch("slurm_quota.client.urlopen", side_effect=URLError("boom")),
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
        ):
            self.run_cli_main_exit(
                [
                    "slurm-quota",
                    "adjust",
                    "--user",
                    "alice",
                    "--cpu",
                    "--minutes",
                    "+10",
                ],
                1,
            )
        self.assertIn("Failed to contact slurm-quota service:", log_cm.output[0])

    def test_adjust_rejects_unsigned_delta(self):
        self.run_cli_main_exit(
            [
                "slurm-quota",
                "adjust",
                "--user",
                "alice",
                "--cpu",
                "--minutes",
                "30",
            ],
            2,
        )

    def test_adjust_reports_api_error_on_missing_target(self):
        def _bad_request(request):
            return FakeNoContentUrlopenResponse(status=400)

        with (
            patch("slurm_quota.client.urlopen", side_effect=_bad_request),
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
        ):
            self.run_cli_main_exit(
                [
                    "slurm-quota",
                    "adjust",
                    "--account",
                    "missing",
                    "--cpu",
                    "--minutes",
                    "+5",
                ],
                1,
            )
        self.assertEqual(
            log_cm.output,
            ["ERROR:slurm_quota:API request failed: HTTP 400"],
        )
