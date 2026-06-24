"""Functional tests: `set-gpu-factor` subcommand."""

from __future__ import annotations

import json
from unittest.mock import patch
from urllib.error import URLError

from slurm_quota.token import service_token_path

from tests.functional.functional_base import (
    FakeNoContentUrlopenResponse,
    FunctionalAPICliBase,
)


class TestSetGpuFactorCommand(FunctionalAPICliBase):
    def _set_gpu_factor_urlopen_side_effect(self, request):
        url = request.full_url
        method = request.get_method()
        if method == "PUT" and url.endswith("/factors/gpu/h100"):
            body = json.loads(request.data.decode("utf-8"))
            self._last_put_body = body
            return FakeNoContentUrlopenResponse(status=204)
        raise AssertionError(f"unexpected request: {method} {url}")

    def setUp(self):
        super().setUp()
        self._last_put_body = {}

    def test_set_gpu_factor_calls_api(self):
        with (
            patch(
                "slurm_quota.client.urlopen",
                side_effect=self._set_gpu_factor_urlopen_side_effect,
            ) as m_urlopen,
            self.capture_stdout() as out,
        ):
            self.run_cli_main(["slurm-quota", "set-gpu-factor", "h100", "0.25"])
        self.assertIn("h100", out.getvalue())
        self.assertEqual(self._last_put_body, {"factor": 0.25})
        req = m_urlopen.call_args[0][0]
        self.assertEqual(req.get_method(), "PUT")
        self.assertTrue(req.full_url.endswith("/factors/gpu/h100"))
        self.assertEqual(req.get_header("Authorization"), "Bearer saved-jwt")

    def test_set_gpu_factor_non_positive_exits(self):
        self.run_cli_main_exit(["slurm-quota", "set-gpu-factor", "x", "0"], 1)

    def test_set_gpu_factor_requires_token(self):
        service_token_path().unlink()
        with self.assertLogs("slurm_quota", level="ERROR") as log_cm:
            self.run_cli_main_exit(
                ["slurm-quota", "set-gpu-factor", "h100", "0.25"],
                1,
            )
        self.assertIn("No API token available", log_cm.output[0])

    def test_set_gpu_factor_reports_access_denied_on_forbidden(self):
        def _forbidden(request):
            return FakeNoContentUrlopenResponse(status=403)

        with (
            patch("slurm_quota.client.urlopen", side_effect=_forbidden),
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
        ):
            self.run_cli_main_exit(
                ["slurm-quota", "set-gpu-factor", "h100", "0.25"],
                1,
            )
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:Access denied: manager or admin role required "
                "to set GPU factors",
            ],
        )

    def test_set_gpu_factor_reports_unreachable_service(self):
        with (
            patch("slurm_quota.client.urlopen", side_effect=URLError("boom")),
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
        ):
            self.run_cli_main_exit(
                ["slurm-quota", "set-gpu-factor", "h100", "0.25"],
                1,
            )
        self.assertIn("Failed to contact slurm-quota service:", log_cm.output[0])
