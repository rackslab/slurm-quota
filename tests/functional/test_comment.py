"""Functional tests: `comments` subcommand and --no-save-comment option."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr
from unittest.mock import patch

from tests.functional.functional_base import (
    FakeJsonUrlopenResponse,
    FakeNoContentUrlopenResponse,
    FunctionalAPICliBase,
)


class TestCommentCommands(FunctionalAPICliBase):
    def _run(self, argv):
        with (
            patch(
                "slurm_quota.client.urlopen",
                return_value=FakeNoContentUrlopenResponse(status=204),
            ) as m_urlopen,
            self.capture_stdout() as out,
        ):
            self.run_cli_main(argv)
        req = m_urlopen.call_args[0][0]
        return out.getvalue(), req, json.loads(req.data.decode("utf-8"))

    def test_user_comment_calls_api(self):
        out, req, body = self._run(
            ["slurm-quota", "comments", "--user", "elena", "PhD student"]
        )
        self.assertEqual(out, "Successfully set comment for user elena\n")
        self.assertEqual(req.get_method(), "PUT")
        self.assertTrue(req.full_url.endswith("/comments/users/elena"))
        self.assertEqual(body, {"comment": "PhD student"})

    def test_account_comment_clear_calls_api(self):
        out, req, body = self._run(
            ["slurm-quota", "comments", "--account", "hpc", "--clear"]
        )
        self.assertEqual(out, "Successfully removed comment of account hpc\n")
        self.assertTrue(req.full_url.endswith("/comments/accounts/hpc"))
        self.assertEqual(body, {"comment": None})

    def test_comment_and_clear_are_exclusive(self):
        with redirect_stderr(io.StringIO()):
            self.run_cli_main_exit(
                ["slurm-quota", "comments", "--user", "elena", "text", "--clear"], 2
            )

    def test_comment_or_clear_is_required(self):
        with redirect_stderr(io.StringIO()):
            self.run_cli_main_exit(["slurm-quota", "comments", "--user", "elena"], 2)

    def test_user_and_account_are_exclusive(self):
        with redirect_stderr(io.StringIO()):
            self.run_cli_main_exit(
                ["slurm-quota", "comments", "--user", "elena", "--account", "hpc", "x"],
                2,
            )

    def test_user_or_account_is_required(self):
        with redirect_stderr(io.StringIO()):
            self.run_cli_main_exit(["slurm-quota", "comments", "text"], 2)

    def test_user_quota_no_append_reason(self):
        _, _, body = self._run(
            [
                "slurm-quota",
                "user-quota",
                "elena",
                "500",
                "--reason",
                "extension",
                "--no-save-comment",
            ]
        )
        self.assertEqual(
            body,
            {"quota_minutes": 500, "reason": "extension", "append_reason": False},
        )

    def test_adjust_no_append_reason(self):
        with (
            patch(
                "slurm_quota.client.urlopen",
                return_value=FakeJsonUrlopenResponse({"total_consumed_seconds": 60}),
            ) as m_urlopen,
            self.capture_stdout(),
        ):
            self.run_cli_main(
                [
                    "slurm-quota",
                    "adjust",
                    "--user",
                    "elena",
                    "--account",
                    "hpc",
                    "--cpu",
                    "--minutes",
                    "+1",
                    "--reason",
                    "fix",
                    "--no-save-comment",
                ]
            )
        body = json.loads(m_urlopen.call_args[0][0].data.decode("utf-8"))
        self.assertEqual(
            body, {"delta_seconds": 60, "reason": "fix", "append_reason": False}
        )
