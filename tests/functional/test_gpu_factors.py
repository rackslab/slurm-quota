"""Functional tests: `gpu-factors` subcommand."""

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


class TestGpuFactorsCommand(FunctionalAPICliBase):
    def _gpu_factors_urlopen_side_effect(self, request, **_kwargs):
        url = request.full_url
        method = request.get_method()
        if method == "GET" and url.endswith("/factors/gpu"):
            return FakeJsonUrlopenResponse(
                {"default_factor": 1.0, "factors": {"a100": 1.5}}
            )
        raise AssertionError(f"unexpected request: {method} {url}")

    def test_gpu_factors_calls_api_and_prints_factors(self):
        with (
            patch(
                "slurm_quota.client.urlopen",
                side_effect=self._gpu_factors_urlopen_side_effect,
            ) as m_urlopen,
            self.capture_stdout() as out,
        ):
            self.run_cli_main(["slurm-quota", "gpu-factors"])
        self.assertEqual(
            out.getvalue(),
            dedent(
                """
                GPU Charging Factors
                ----------------------------------------
                default              1.000
                a100                 1.500
                """
            ).lstrip("\n"),
        )
        req = m_urlopen.call_args[0][0]
        self.assertEqual(req.get_method(), "GET")
        self.assertTrue(req.full_url.endswith("/factors/gpu"))
        self.assertEqual(req.get_header("Authorization"), "Bearer saved-jwt")

    def test_gpu_factors_requires_token(self):
        service_token_path().unlink()
        with self.assertLogs("slurm_quota", level="ERROR") as log_cm:
            self.run_cli_main_exit(["slurm-quota", "gpu-factors"], 1)
        self.assertIn("No API token available", log_cm.output[0])

    def test_gpu_factors_reports_access_denied_on_forbidden(self):
        def _forbidden(request, **_kwargs):
            raise fake_http_error(403, url=request.full_url)

        with (
            patch("slurm_quota.client.urlopen", side_effect=_forbidden),
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
        ):
            self.run_cli_main_exit(["slurm-quota", "gpu-factors"], 1)
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:Access denied: operator or admin role required "
                "to view GPU factors",
            ],
        )

    def test_gpu_factors_reports_unreachable_service(self):
        with (
            patch("slurm_quota.client.urlopen", side_effect=URLError("boom")),
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
        ):
            self.run_cli_main_exit(["slurm-quota", "gpu-factors"], 1)
        self.assertIn("Failed to contact slurm-quota service:", log_cm.output[0])
