"""Functional tests: `set-default-quotas` subcommand."""

from __future__ import annotations

import json
from unittest.mock import patch
from urllib.error import URLError

from slurm_quota.token import service_token_path
from tests.functional.functional_base import (
    FakeNoContentUrlopenResponse,
    FunctionalAPICliBase,
)
from tests.testing_utils import fake_http_error


class TestSetDefaultQuotasCommand(FunctionalAPICliBase):
    def _set_default_quotas_urlopen_side_effect(self, request, **_kwargs):
        url = request.full_url
        method = request.get_method()
        if method == "PUT" and url.endswith("/quotas/defaults"):
            body = json.loads(request.data.decode("utf-8"))
            self._last_put_body = body
            return FakeNoContentUrlopenResponse(status=204)
        raise AssertionError(f"unexpected request: {method} {url}")

    def setUp(self):
        super().setUp()
        self._last_put_body = {}

    def test_set_default_quotas_calls_api_with_all_keys(self):
        with (
            patch(
                "slurm_quota.client.urlopen",
                side_effect=self._set_default_quotas_urlopen_side_effect,
            ) as m_urlopen,
            self.capture_stdout() as out,
        ):
            self.run_cli_main(
                [
                    "slurm-quota",
                    "set-default-quotas",
                    "--user-cpu",
                    "10",
                    "--user-gpu",
                    "20",
                    "--account-cpu",
                    "30",
                    "--account-gpu",
                    "40",
                ]
            )
        self.assertEqual(
            out.getvalue(),
            "Successfully updated default quotas\n",
        )
        self.assertEqual(
            self._last_put_body,
            {
                "user_cpu_minutes": 10,
                "user_gpu_minutes": 20,
                "account_cpu_minutes": 30,
                "account_gpu_minutes": 40,
            },
        )
        req = m_urlopen.call_args[0][0]
        self.assertEqual(req.get_method(), "PUT")
        self.assertTrue(req.full_url.endswith("/quotas/defaults"))
        self.assertEqual(req.get_header("Authorization"), "Bearer saved-jwt")

    def test_set_default_quotas_partial_only_sends_given_keys(self):
        with (
            patch(
                "slurm_quota.client.urlopen",
                side_effect=self._set_default_quotas_urlopen_side_effect,
            ),
            self.capture_stdout() as out,
        ):
            self.run_cli_main(
                ["slurm-quota", "set-default-quotas", "--user-cpu", "999"]
            )
        self.assertEqual(
            out.getvalue(),
            "Successfully updated default quotas\n",
        )
        self.assertEqual(self._last_put_body, {"user_cpu_minutes": 999})

    def test_set_default_quotas_requires_token(self):
        service_token_path().unlink()
        with self.assertLogs("slurm_quota", level="ERROR") as log_cm:
            self.run_cli_main_exit(
                ["slurm-quota", "set-default-quotas", "--user-cpu", "1"],
                1,
            )
        self.assertIn("No API token available", log_cm.output[0])

    def test_set_default_quotas_reports_access_denied_on_forbidden(self):
        def _forbidden(request, **_kwargs):
            raise fake_http_error(403, url=request.full_url)

        with (
            patch("slurm_quota.client.urlopen", side_effect=_forbidden),
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
        ):
            self.run_cli_main_exit(
                ["slurm-quota", "set-default-quotas", "--user-cpu", "1"],
                1,
            )
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:Access denied: operator or admin role required "
                "to set default quotas",
            ],
        )

    def test_set_default_quotas_requires_at_least_one_flag(self):
        with self.assertLogs("slurm_quota", level="ERROR") as log_cm:
            self.run_cli_main_exit(["slurm-quota", "set-default-quotas"], 1)
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:At least one option must be provided: --user-cpu, "
                "--user-gpu, --account-cpu, --account-gpu",
            ],
        )

    def test_set_default_quotas_reports_unreachable_service(self):
        with (
            patch("slurm_quota.client.urlopen", side_effect=URLError("boom")),
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
        ):
            self.run_cli_main_exit(
                ["slurm-quota", "set-default-quotas", "--user-cpu", "1"],
                1,
            )
        self.assertIn("Failed to contact slurm-quota service:", log_cm.output[0])
