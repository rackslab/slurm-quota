"""Functional tests: `adjust` subcommand."""

from __future__ import annotations

import json
from unittest.mock import patch
from urllib.error import URLError

from slurm_quota.token import ClientToken
from tests.functional.functional_base import (
    FakeJsonUrlopenResponse,
    FunctionalAPICliBase,
)
from tests.testing_utils import fake_http_error


class TestAdjustCommand(FunctionalAPICliBase):
    def _adjust_urlopen_side_effect(self, request, **_kwargs):
        url = request.full_url
        method = request.get_method()
        if method == "PATCH" and url.endswith("/consumption/alice/hpc/cpu"):
            body = json.loads(request.data.decode("utf-8"))
            self.assertIn("delta_seconds", body)
            return FakeJsonUrlopenResponse({"total_consumed_seconds": 7800})
        if method == "PATCH" and url.endswith("/consumption/alice/hpc/gpu"):
            body = json.loads(request.data.decode("utf-8"))
            self.assertIn("delta_seconds", body)
            return FakeJsonUrlopenResponse({"total_consumed_seconds": 12630})
        raise AssertionError(f"unexpected request: {method} {url}")

    def test_adjust_pair_cpu_minutes(self):
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
                    "--account",
                    "hpc",
                    "--cpu",
                    "--minutes",
                    "+30",
                ]
            )
        self.assertEqual(
            out.getvalue(),
            (
                "Successfully adjusted CPU consumed time for user alice on "
                "account hpc: "
                "+30 minutes (new total: 130 minutes)\n"
            ),
        )
        req = m_urlopen.call_args[0][0]
        self.assertEqual(req.get_method(), "PATCH")
        self.assertTrue(req.full_url.endswith("/consumption/alice/hpc/cpu"))
        self.assertEqual(req.get_header("Authorization"), "Bearer saved-jwt")
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body, {"delta_seconds": 1800})

    def test_adjust_pair_gpu_hours(self):
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
                "Successfully adjusted GPU consumed time for user alice on "
                "account hpc: "
                "+2 hours (new total: 210 minutes)\n"
            ),
        )
        req = m_urlopen.call_args[0][0]
        self.assertTrue(req.full_url.endswith("/consumption/alice/hpc/gpu"))
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body, {"delta_seconds": 7200})

    def test_adjust_pair_cpu_seconds(self):
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
                    "--account",
                    "hpc",
                    "--cpu",
                    "--seconds",
                    "-45",
                ]
            )
        self.assertEqual(
            out.getvalue(),
            (
                "Successfully adjusted CPU consumed time for user alice on "
                "account hpc: "
                "-45 seconds (new total: 7800 seconds)\n"
            ),
        )
        body = json.loads(m_urlopen.call_args[0][0].data.decode("utf-8"))
        self.assertEqual(body, {"delta_seconds": -45})

    def test_adjust_requires_token(self):
        ClientToken.path().unlink()
        with self.assertLogs("slurm_quota", level="ERROR") as log_cm:
            self.run_cli_main_exit(
                [
                    "slurm-quota",
                    "adjust",
                    "--user",
                    "alice",
                    "--account",
                    "hpc",
                    "--cpu",
                    "--minutes",
                    "+10",
                ],
                1,
            )
        self.assertIn("No API token available", log_cm.output[0])

    def test_adjust_reports_access_denied_on_forbidden(self):
        def _forbidden(request, **_kwargs):
            raise fake_http_error(403, url=request.full_url)

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
                    "--account",
                    "hpc",
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
                    "--account",
                    "hpc",
                    "--cpu",
                    "--minutes",
                    "+10",
                ],
                1,
            )
        self.assertIn("Unable to reach slurm-quota REST API at", log_cm.output[0])

    def test_adjust_rejects_unsigned_delta(self):
        self.run_cli_main_exit(
            [
                "slurm-quota",
                "adjust",
                "--user",
                "alice",
                "--account",
                "hpc",
                "--cpu",
                "--minutes",
                "30",
            ],
            2,
        )

    def test_adjust_requires_user_and_account(self):
        for argv in (
            ["slurm-quota", "adjust", "--user", "alice", "--cpu", "--minutes", "+1"],
            ["slurm-quota", "adjust", "--account", "hpc", "--cpu", "--minutes", "+1"],
        ):
            with self.subTest(argv=argv):
                self.run_cli_main_exit(argv, 2)

    def test_adjust_reports_api_error_on_missing_target(self):
        def _bad_request(request, **_kwargs):
            raise fake_http_error(400, url=request.full_url)

        with (
            patch("slurm_quota.client.urlopen", side_effect=_bad_request),
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
        ):
            self.run_cli_main_exit(
                [
                    "slurm-quota",
                    "adjust",
                    "--user",
                    "alice",
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
