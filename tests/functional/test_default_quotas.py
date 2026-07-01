"""Functional tests: `default-quotas` subcommand."""

from __future__ import annotations

from textwrap import dedent
from unittest.mock import patch
from urllib.error import URLError

from slurm_quota.token import service_token_path
from tests.functional.functional_base import (
    FakeJsonUrlopenResponse,
    FunctionalAPICliBase,
)
from tests.testing_utils import fake_http_error


class TestDefaultQuotasCommand(FunctionalAPICliBase):
    def _default_quotas_urlopen_side_effect(self, request, **_kwargs):
        url = request.full_url
        method = request.get_method()
        if method == "GET" and url.endswith("/quotas/defaults"):
            return FakeJsonUrlopenResponse(
                {
                    "user_cpu_minutes": -1,
                    "user_gpu_minutes": -1,
                    "account_cpu_minutes": -1,
                    "account_gpu_minutes": -1,
                }
            )
        raise AssertionError(f"unexpected request: {method} {url}")

    def test_default_quotas_calls_api_and_prints_defaults(self):
        with (
            patch(
                "slurm_quota.client.urlopen",
                side_effect=self._default_quotas_urlopen_side_effect,
            ) as m_urlopen,
            self.capture_stdout() as out,
        ):
            self.run_cli_main(["slurm-quota", "default-quotas"])
        self.assertEqual(
            out.getvalue(),
            dedent(
                """
                Default Quotas For New Entities
                ----------------------------------------
                user_cpu_minutes         ∞
                user_gpu_minutes         ∞
                account_cpu_minutes      ∞
                account_gpu_minutes      ∞
                """
            ).lstrip("\n"),
        )
        req = m_urlopen.call_args[0][0]
        self.assertEqual(req.get_method(), "GET")
        self.assertTrue(req.full_url.endswith("/quotas/defaults"))
        self.assertEqual(req.get_header("Authorization"), "Bearer saved-jwt")

    def test_default_quotas_reflects_api_payload(self):
        def _custom_payload(request, **_kwargs):
            if request.get_method() == "GET" and request.full_url.endswith(
                "/quotas/defaults"
            ):
                return FakeJsonUrlopenResponse(
                    {
                        "user_cpu_minutes": 100,
                        "user_gpu_minutes": 200,
                        "account_cpu_minutes": 300,
                        "account_gpu_minutes": 400,
                    }
                )
            raise AssertionError(
                f"unexpected request: {request.get_method()} {request.full_url}"
            )

        with (
            patch("slurm_quota.client.urlopen", side_effect=_custom_payload),
            self.capture_stdout() as out,
        ):
            self.run_cli_main(["slurm-quota", "default-quotas"])
        self.assertEqual(
            out.getvalue(),
            dedent(
                """
                Default Quotas For New Entities
                ----------------------------------------
                user_cpu_minutes         100
                user_gpu_minutes         200
                account_cpu_minutes      300
                account_gpu_minutes      400
                """
            ).lstrip("\n"),
        )

    def test_default_quotas_shows_infinity_only_for_negative_one(self):
        def _custom_payload(request, **_kwargs):
            if request.get_method() == "GET" and request.full_url.endswith(
                "/quotas/defaults"
            ):
                return FakeJsonUrlopenResponse(
                    {
                        "user_cpu_minutes": -1,
                        "user_gpu_minutes": 0,
                        "account_cpu_minutes": -1,
                        "account_gpu_minutes": 42,
                    }
                )
            raise AssertionError(
                f"unexpected request: {request.get_method()} {request.full_url}"
            )

        with (
            patch("slurm_quota.client.urlopen", side_effect=_custom_payload),
            self.capture_stdout() as out,
        ):
            self.run_cli_main(["slurm-quota", "default-quotas"])
        self.assertEqual(
            out.getvalue(),
            dedent(
                """
                Default Quotas For New Entities
                ----------------------------------------
                user_cpu_minutes         ∞
                user_gpu_minutes         0
                account_cpu_minutes      ∞
                account_gpu_minutes      42
                """
            ).lstrip("\n"),
        )

    def test_default_quotas_requires_token(self):
        service_token_path().unlink()
        with self.assertLogs("slurm_quota", level="ERROR") as log_cm:
            self.run_cli_main_exit(["slurm-quota", "default-quotas"], 1)
        self.assertIn("No API token available", log_cm.output[0])

    def test_default_quotas_reports_access_denied_on_forbidden(self):
        def _forbidden(request, **_kwargs):
            raise fake_http_error(403, url=request.full_url)

        with (
            patch("slurm_quota.client.urlopen", side_effect=_forbidden),
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
        ):
            self.run_cli_main_exit(["slurm-quota", "default-quotas"], 1)
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:Access denied: operator or admin role required "
                "to view default quotas",
            ],
        )

    def test_default_quotas_reports_unreachable_service(self):
        with (
            patch("slurm_quota.client.urlopen", side_effect=URLError("boom")),
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
        ):
            self.run_cli_main_exit(["slurm-quota", "default-quotas"], 1)
        self.assertIn("Failed to contact slurm-quota service:", log_cm.output[0])
