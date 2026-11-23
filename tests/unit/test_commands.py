"""Unit tests for slurm_quota.commands."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from slurm_quota.client import ServiceHTTPError, ServiceUnreachableError
from slurm_quota.commands import (
    RELOGIN_GUIDANCE,
    create_status_bar,
    prune_command,
    role_grant_command,
    role_list_command,
    role_managers_add_command,
    role_managers_list_command,
    role_managers_remove_command,
    role_revoke_command,
    role_show_command,
    set_account_comment_command,
    set_user_comment_command,
    show_user_stats,
    token_command,
)
from slurm_quota.token import ClientToken, TokenPayload
from tests.test_support import SlurmQuotaTestCase
from tests.testing_utils import craft_jwt, dedent_lines


class TestCreateStatusBar(SlurmQuotaTestCase):
    def test_create_status_bar(self):
        self.assertEqual(create_status_bar(0, 0), " " * 20)
        self.assertIn("50.0%", create_status_bar(5, 10))
        self.assertIn("100.0%", create_status_bar(15, 10))


def _sample_users_and_accounts():
    users = [
        {
            "username": "alice",
            "job_count": 2,
            "last_updated": "2024-06-01T10:00:00",
            "total_consumed_cpu_seconds": 7200,
            "total_preallocated_cpu_seconds": 3600,
            "quota_cpu_minutes": 600,
            "total_consumed_gpu_seconds": 0,
            "total_preallocated_gpu_seconds": 0,
            "quota_gpu_minutes": -1,
        }
    ]
    accounts = [
        {
            "account": "acct1",
            "job_count": 0,
            "last_updated": None,
            "total_consumed_cpu_seconds": 60,
            "total_preallocated_cpu_seconds": 0,
            "quota_cpu_minutes": -1,
            "total_consumed_gpu_seconds": 0,
            "total_preallocated_gpu_seconds": 0,
            "quota_gpu_minutes": -1,
        }
    ]
    return users, accounts


class TestShowUserStats(SlurmQuotaTestCase):
    def _run_show(self, **kwargs):
        buf = io.StringIO()
        with redirect_stdout(buf):
            show_user_stats(**kwargs)
        return buf.getvalue()

    def _mock_stats(self, return_value=None, side_effect=None):
        load_patcher = patch(
            "slurm_quota.commands.ClientToken.load_value", return_value="token"
        )
        load_patcher.start()
        self.addCleanup(load_patcher.stop)
        patcher = patch("slurm_quota.commands.APIClient")
        m_api = patcher.start()
        self.addCleanup(patcher.stop)
        if side_effect is not None:
            m_api.return_value.stats.side_effect = side_effect
        else:
            m_api.return_value.stats.return_value = return_value
        return m_api

    def test_no_data_for_explicit_username(self):
        self._mock_stats(return_value=([], [], []))
        out = self._run_show(username="bob", show_all=False)
        expected = dedent_lines("No data found for user: bob")
        self.assertEqual(out, expected)

    def test_no_data_for_explicit_account(self):
        self._mock_stats(return_value=([], [], []))
        out = self._run_show(account="projX", show_all=False)
        expected = dedent_lines("No data found for account: projX")
        self.assertEqual(out, expected)

    def test_no_users_when_show_all_empty(self):
        self._mock_stats(return_value=([], [], []))
        out = self._run_show(show_all=True)
        expected = dedent_lines("No users found in service")
        self.assertEqual(out, expected)

    def test_prints_user_and_account_tables(self):
        self.env({"NO_COLOR": "1"})
        users, accounts = _sample_users_and_accounts()
        self._mock_stats(return_value=(users, accounts, []))
        with patch(
            "slurm_quota.commands.format_timestamp_with_timezone",
            return_value="TS_FIXED",
        ):
            out = self._run_show(username="alice", show_all=False)
        expected = dedent_lines(
            "        |                                CPU                                 |                                GPU                                 |",
            "USER    |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
            "----------------------------------------------------------------------------------------------------------------------------------------------------------------",
            "alice   |         120           60(2)      600 [██████░░░░░░░░░░░░░░]  30.0% |           0            0(2)        ∞                               | TS_FIXED                 ",
            "",
            "        |                                CPU                                 |                                GPU                                 |",
            "ACCOUNT |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
            "----------------------------------------------------------------------------------------------------------------------------------------------------------------",
            "acct1   |           1            0(0)        ∞                               |           0            0(0)        ∞                               | TS_FIXED                 ",
        )
        self.assertEqual(out, expected)

    def test_prints_accounts_only_when_users_empty_for_account_scope(self):
        """Account-filtered responses omit user rows; only the accounts table is printed."""
        self.env({"NO_COLOR": "1"})
        _, accounts = _sample_users_and_accounts()
        self._mock_stats(return_value=([], accounts, []))
        with patch(
            "slurm_quota.commands.format_timestamp_with_timezone",
            return_value="TS_FIXED",
        ):
            out = self._run_show(account="acct1", show_all=False)
        expected = dedent_lines(
            "        |                                CPU                                 |                                GPU                                 |",
            "ACCOUNT |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
            "----------------------------------------------------------------------------------------------------------------------------------------------------------------",
            "acct1   |           1            0(0)        ∞                               |           0            0(0)        ∞                               | TS_FIXED                 ",
        )
        self.assertEqual(out, expected)

    def _breakdown_entries(self):
        return [
            {
                "username": "alice",
                "account": "acct1",
                "consumed_cpu_seconds": 3600,
                "consumed_gpu_seconds": 60,
                "last_updated": "2024-01-15T12:30:45",
            },
            {
                "username": "alice",
                "account": "(unattributed)",
                "consumed_cpu_seconds": 3600,
                "consumed_gpu_seconds": 0,
                "last_updated": "2024-01-15T12:30:45",
            },
        ]

    def test_user_consumption_broken_down_by_account(self):
        self.env({"NO_COLOR": "1"})
        users, accounts = _sample_users_and_accounts()
        m_api = self._mock_stats(
            return_value=(users, accounts, self._breakdown_entries())
        )
        with patch(
            "slurm_quota.commands.format_timestamp_with_timezone",
            return_value="TS_FIXED",
        ):
            out = self._run_show(username="alice", show_all=False)
        m_api.return_value.stats.assert_called_once_with(
            "alice", None, False, include_breakdown=True
        )
        lines = out.splitlines()
        self.assertTrue(lines[3].startswith("alice              |"), lines[3])
        label, cpu, gpu, last_updated = lines[4].split("|")
        self.assertEqual(label, "  └ acct1          ")
        # Only consumption is reported, no preallocation, quota nor status
        self.assertEqual(cpu.split(), ["60"])
        self.assertEqual(gpu.split(), ["1"])
        self.assertEqual(last_updated.strip(), "TS_FIXED")
        self.assertTrue(lines[5].startswith("  └ (unattributed) |          60 "))
        # Account rows are not broken down for a user query
        self.assertTrue(lines[-1].startswith("acct1              |"), lines[-1])

    def test_comments_printed_below_rows(self):
        self.env({"NO_COLOR": "1"})
        users, accounts = _sample_users_and_accounts()
        users[0]["comment"] = "PhD student"
        accounts[0]["comment"] = "Project X"
        self._mock_stats(return_value=(users, accounts, self._breakdown_entries()))
        out = self._run_show(username="alice", show_all=False, show_comments=True)
        lines = out.splitlines()
        self.assertTrue(lines[3].startswith("alice              |"), lines[3])
        # Comment is printed before the breakdown of the row
        self.assertEqual(lines[4], f"{'':<18}   # PhD student")
        self.assertTrue(lines[5].startswith("  └ acct1"), lines[5])
        self.assertTrue(lines[-2].startswith("acct1              |"), lines[-2])
        self.assertEqual(lines[-1], f"{'':<18}   # Project X")

    def test_comments_hidden_by_default(self):
        self.env({"NO_COLOR": "1"})
        users, accounts = _sample_users_and_accounts()
        users[0]["comment"] = "PhD student"
        accounts[0]["comment"] = "Project X"
        self._mock_stats(return_value=(users, accounts, self._breakdown_entries()))
        out = self._run_show(username="alice", show_all=False)
        self.assertNotIn("#", out)

    def test_account_consumption_broken_down_by_user(self):
        self.env({"NO_COLOR": "1"})
        _, accounts = _sample_users_and_accounts()
        entries = self._breakdown_entries()[:1]
        self._mock_stats(return_value=([], accounts, entries))
        with patch(
            "slurm_quota.commands.format_timestamp_with_timezone",
            return_value="TS_FIXED",
        ):
            out = self._run_show(account="acct1", show_all=False)
        lines = out.splitlines()
        self.assertTrue(lines[3].startswith("acct1     |"), lines[3])
        self.assertTrue(lines[4].startswith("  └ alice |          60 "), lines[4])
        self.assertEqual(len(lines), 5)

    def test_all_without_breakdown_option(self):
        self.env({"NO_COLOR": "1"})
        users, accounts = _sample_users_and_accounts()
        m_api = self._mock_stats(return_value=(users, accounts, []))
        out = self._run_show(show_all=True)
        m_api.return_value.stats.assert_called_once_with(
            None, None, True, include_breakdown=False
        )
        self.assertNotIn("└", out)

    def test_all_with_breakdown_option(self):
        self.env({"NO_COLOR": "1"})
        users, accounts = _sample_users_and_accounts()
        entries = self._breakdown_entries()[:1]
        m_api = self._mock_stats(return_value=(users, accounts, entries))
        out = self._run_show(show_all=True, show_breakdown=True)
        m_api.return_value.stats.assert_called_once_with(
            None, None, True, include_breakdown=True
        )
        labels = [line.split("|")[0].rstrip() for line in out.splitlines()]
        self.assertEqual(labels[labels.index("alice") + 1], "  └ acct1")
        self.assertEqual(labels[labels.index("acct1") + 1], "  └ alice")

    def test_user_breakdown_disabled(self):
        self.env({"NO_COLOR": "1"})
        users, accounts = _sample_users_and_accounts()
        m_api = self._mock_stats(return_value=(users, accounts, []))
        out = self._run_show(username="alice", show_breakdown=False)
        m_api.return_value.stats.assert_called_once_with(
            "alice", None, False, include_breakdown=False
        )
        self.assertNotIn("└", out)

    def test_account_breakdown_disabled(self):
        self.env({"NO_COLOR": "1"})
        _, accounts = _sample_users_and_accounts()
        m_api = self._mock_stats(return_value=([], accounts, []))
        out = self._run_show(account="acct1", show_breakdown=False)
        m_api.return_value.stats.assert_called_once_with(
            None, "acct1", False, include_breakdown=False
        )
        self.assertNotIn("└", out)

    def test_display_hours_formats_numbers(self):
        self.env({"NO_COLOR": "1"})
        user = {
            "username": "u1",
            "job_count": 0,
            "last_updated": None,
            "total_consumed_cpu_seconds": 7200,
            "total_preallocated_cpu_seconds": 0,
            "quota_cpu_minutes": -1,
            "total_consumed_gpu_seconds": 0,
            "total_preallocated_gpu_seconds": 0,
            "quota_gpu_minutes": -1,
        }
        self._mock_stats(return_value=([user], [], []))
        with patch(
            "slurm_quota.commands.format_timestamp_with_timezone",
            return_value="TS_FIXED",
        ):
            out = self._run_show(username="u1", show_all=False, display_unit="hours")
        expected = dedent_lines(
            "        |                                CPU                                 |                                GPU                                 |",
            "USER    |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
            "----------------------------------------------------------------------------------------------------------------------------------------------------------------",
            "u1      |        2.00         0.00(0)        ∞                               |        0.00         0.00(0)        ∞                               | TS_FIXED                 ",
            "",
            "        |                                CPU                                 |                                GPU                                 |",
            "ACCOUNT |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
            "----------------------------------------------------------------------------------------------------------------------------------------------------------------",
        )
        self.assertEqual(out, expected)

    def test_display_seconds_formats_numbers(self):
        self.env({"NO_COLOR": "1"})
        user = {
            "username": "u1",
            "job_count": 1,
            "last_updated": None,
            "total_consumed_cpu_seconds": 7259,
            "total_preallocated_cpu_seconds": 30,
            "quota_cpu_minutes": 100,
            "total_consumed_gpu_seconds": 0,
            "total_preallocated_gpu_seconds": 0,
            "quota_gpu_minutes": -1,
        }
        self._mock_stats(return_value=([user], [], []))
        with patch(
            "slurm_quota.commands.format_timestamp_with_timezone",
            return_value="TS_FIXED",
        ):
            out = self._run_show(username="u1", show_all=False, display_unit="seconds")
        row = out.splitlines()[3]
        self.assertTrue(
            row.startswith("u1      |        7259           30(1)     6000 "), row
        )
        self.assertIn("|           0            0(1)        ∞ ", row)

    def test_finite_quota_shows_status_bar(self):
        self.env({"NO_COLOR": "1"})
        user = {
            "username": "u1",
            "job_count": 0,
            "last_updated": None,
            "total_consumed_cpu_seconds": 3000,
            "total_preallocated_cpu_seconds": 0,
            "quota_cpu_minutes": 100,
            "total_consumed_gpu_seconds": 0,
            "total_preallocated_gpu_seconds": 0,
            "quota_gpu_minutes": -1,
        }
        self._mock_stats(return_value=([user], [], []))
        with patch(
            "slurm_quota.commands.format_timestamp_with_timezone",
            return_value="TS_FIXED",
        ):
            out = self._run_show(username="u1", show_all=False)
        expected = dedent_lines(
            "        |                                CPU                                 |                                GPU                                 |",
            "USER    |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
            "----------------------------------------------------------------------------------------------------------------------------------------------------------------",
            "u1      |          50            0(0)      100 [██████████░░░░░░░░░░]  50.0% |           0            0(0)        ∞                               | TS_FIXED                 ",
            "",
            "        |                                CPU                                 |                                GPU                                 |",
            "ACCOUNT |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
            "----------------------------------------------------------------------------------------------------------------------------------------------------------------",
        )
        self.assertEqual(out, expected)

    def test_status_bar_is_computed_in_seconds(self):
        self.env({"NO_COLOR": "1"})
        # 9 min 59 s consumed + 30 s preallocated exceed the 10 min quota, while
        # displayed floored minutes (9 + 0) would only fill 90%.
        user = {
            "username": "u1",
            "job_count": 1,
            "last_updated": None,
            "total_consumed_cpu_seconds": 599,
            "total_preallocated_cpu_seconds": 30,
            "quota_cpu_minutes": 10,
            "total_consumed_gpu_seconds": 0,
            "total_preallocated_gpu_seconds": 0,
            "quota_gpu_minutes": -1,
        }
        self._mock_stats(return_value=([user], [], []))
        with patch(
            "slurm_quota.commands.format_timestamp_with_timezone",
            return_value="TS_FIXED",
        ):
            out = self._run_show(username="u1", show_all=False)
        self.assertIn(
            "u1      |           9            0(1)       10 [████████████████████] 100.0% |",
            out,
        )

    def test_first_column_cap_and_ellipsis_align_user_and_account_tables(self):
        self.env({"NO_COLOR": "1"})
        users = [
            {
                "username": "user_with_a_name_longer_than_thirty_chars",
                "job_count": 1,
                "last_updated": None,
                "total_consumed_cpu_seconds": 60,
                "total_preallocated_cpu_seconds": 60,
                "quota_cpu_minutes": -1,
                "total_consumed_gpu_seconds": 0,
                "total_preallocated_gpu_seconds": 0,
                "quota_gpu_minutes": -1,
            }
        ]
        accounts = [
            {
                "account": "account_with_a_name_longer_than_thirty_chars",
                "job_count": 1,
                "last_updated": None,
                "total_consumed_cpu_seconds": 60,
                "total_preallocated_cpu_seconds": 60,
                "quota_cpu_minutes": -1,
                "total_consumed_gpu_seconds": 0,
                "total_preallocated_gpu_seconds": 0,
                "quota_gpu_minutes": -1,
            }
        ]
        self._mock_stats(return_value=(users, accounts, []))
        with patch(
            "slurm_quota.commands.format_timestamp_with_timezone",
            return_value="TS_FIXED",
        ):
            out = self._run_show(show_all=True)

        expected = dedent_lines(
            "                               |                                CPU                                 |                                GPU                                 |",
            "USER                           |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
            "---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
            "user_with_a_name_longer_than_… |           1            1(1)        ∞                               |           0            0(1)        ∞                               | TS_FIXED                 ",
            "",
            "                               |                                CPU                                 |                                GPU                                 |",
            "ACCOUNT                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
            "---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
            "account_with_a_name_longer_th… |           1            1(1)        ∞                               |           0            0(1)        ∞                               | TS_FIXED                 ",
        )
        self.assertEqual(out, expected)

    def test_uses_get_current_user_when_username_omitted(self):
        with patch("slurm_quota.auth.get_current_user", return_value="carol") as m_gc:
            m_api = self._mock_stats(return_value=([], [], []))
            out = self._run_show(show_all=False)
        m_gc.assert_called_once()
        m_api.return_value.stats.assert_called_once_with(
            "carol", None, False, include_breakdown=True
        )
        self.assertEqual(out, dedent_lines("No users found in service"))

    def test_skips_get_current_user_when_account_requested(self):
        with patch("slurm_quota.auth.get_current_user") as m_gc:
            m_api = self._mock_stats(return_value=([], [], []))
            out = self._run_show(account="hpc", show_all=False)
        m_gc.assert_not_called()
        m_api.return_value.stats.assert_called_once_with(
            None, "hpc", False, include_breakdown=True
        )
        self.assertEqual(out, dedent_lines("No data found for account: hpc"))

    def test_get_current_user_keyerror_exits(self):
        with (
            patch("slurm_quota.auth.get_current_user", side_effect=KeyError),
            self.assertRaises(SystemExit) as cm,
        ):
            show_user_stats(show_all=False)
        self.assertEqual(cm.exception.code, 1)

    def test_show_user_stats_exits_when_no_token(self):
        config_home = Path(self._tmp.name) / "empty-config"
        self.env({"XDG_CONFIG_HOME": str(config_home), "SLURM_QUOTA_TOKEN": ""})
        with (
            patch("slurm_quota.commands.ClientToken.load_value", return_value=None),
            patch("slurm_quota.commands.APIClient") as m_api,
            self.assertLogs("slurm_quota", level="INFO") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            show_user_stats(username="alice", show_all=False)
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("No API token available", log_cm.output[0])
        self.assertIn(RELOGIN_GUIDANCE, log_cm.output[1])
        m_api.assert_not_called()

    def test_stats_expired_token_reports_relogin_guidance(self):
        self._mock_stats(side_effect=ServiceHTTPError(401, message="Token is expired"))
        with (
            self.assertLogs("slurm_quota", level="INFO") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            show_user_stats(username="x", show_all=False)
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("expired", log_cm.output[0].lower())
        self.assertIn(RELOGIN_GUIDANCE, log_cm.output[1])

    def test_stats_http_error_exits(self):
        self._mock_stats(side_effect=ServiceHTTPError(502))
        with self.assertRaises(SystemExit) as cm:
            show_user_stats(username="x", show_all=False)
        self.assertEqual(cm.exception.code, 1)

    def test_urlerror_exits(self):
        self._mock_stats(
            side_effect=ServiceUnreachableError("http://api:9911/", "down")
        )
        with self.assertRaises(SystemExit) as cm:
            show_user_stats(username="x", show_all=False)
        self.assertEqual(cm.exception.code, 1)

    def test_generic_exception_exits(self):
        self._mock_stats(side_effect=ValueError("bad json"))
        with self.assertRaises(SystemExit) as cm:
            show_user_stats(username="x", show_all=False)
        self.assertEqual(cm.exception.code, 1)


class TestCommentCommands(SlurmQuotaTestCase):
    def _run(self, command, *args):
        buf = io.StringIO()
        with (
            patch("slurm_quota.commands.ClientToken.load_value", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            redirect_stdout(buf),
        ):
            command(*args)
        return buf.getvalue(), m_client.return_value

    def test_set_user_comment(self):
        out, api = self._run(set_user_comment_command, "bob", "PhD student")
        self.assertEqual(out, "Successfully set comment for user bob\n")
        api.set_user_comment.assert_called_once_with("bob", "PhD student")

    def test_clear_account_comment(self):
        out, api = self._run(set_account_comment_command, "hpc", None)
        self.assertEqual(out, "Successfully removed comment of account hpc\n")
        api.set_account_comment.assert_called_once_with("hpc", None)

    def test_set_comment_reports_forbidden(self):
        with (
            patch("slurm_quota.commands.ClientToken.load_value", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            m_client.return_value.set_user_comment.side_effect = ServiceHTTPError(403)
            set_user_comment_command("bob", "hello")
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:Access denied: operator or admin role required "
                "to set comments"
            ],
        )


class TestRoleCommands(SlurmQuotaTestCase):
    def test_role_show_prints_username_and_role(self):
        buf = io.StringIO()
        with (
            patch("slurm_quota.commands.ClientToken.load_value", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            redirect_stdout(buf),
        ):
            m_client.return_value.me.return_value = {
                "username": "alice",
                "role": "admin",
            }
            role_show_command()
        output = buf.getvalue()
        self.assertIn("alice", output)
        self.assertIn("admin", output)

    def test_role_show_expired_token_reports_relogin_guidance(self):
        with (
            patch("slurm_quota.commands.ClientToken.load_value", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            self.assertLogs("slurm_quota", level="INFO") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            m_client.return_value.me.side_effect = ServiceHTTPError(
                401, message="Token is expired"
            )
            role_show_command()
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("expired", log_cm.output[0].lower())
        self.assertIn(RELOGIN_GUIDANCE, log_cm.output[1])

    def test_role_show_invalid_token_reports_auth_failure(self):
        with (
            patch("slurm_quota.commands.ClientToken.load_value", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            self.assertLogs("slurm_quota", level="INFO") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            m_client.return_value.me.side_effect = ServiceHTTPError(
                401, message="Token signature is invalid"
            )
            role_show_command()
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("Token signature is invalid", log_cm.output[0])
        self.assertIn(RELOGIN_GUIDANCE, log_cm.output[1])

    def test_role_show_generic_http_error(self):
        with (
            patch("slurm_quota.commands.ClientToken.load_value", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            m_client.return_value.me.side_effect = ServiceHTTPError(502)
            role_show_command()
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            ["ERROR:slurm_quota:API request failed: HTTP 502"],
        )

    def test_role_show_http_error_includes_api_message(self):
        with (
            patch("slurm_quota.commands.ClientToken.load_value", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            m_client.return_value.me.side_effect = ServiceHTTPError(
                400, message="Invalid account"
            )
            role_show_command()
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            ["ERROR:slurm_quota:API request failed: HTTP 400: Invalid account"],
        )

    def test_role_list_prints_table(self):
        buf = io.StringIO()
        with (
            patch("slurm_quota.commands.ClientToken.load_value", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            redirect_stdout(buf),
        ):
            m_client.return_value.users_roles.return_value = [
                {"username": "alice", "role": "admin"},
                {"username": "bob", "role": "user"},
            ]
            role_list_command()
        output = buf.getvalue()
        self.assertIn("USERNAME", output)
        self.assertIn("alice", output)
        self.assertIn("bob", output)

    def test_role_grant_prints_confirmation(self):
        buf = io.StringIO()
        with (
            patch("slurm_quota.commands.ClientToken.load_value", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            redirect_stdout(buf),
        ):
            role_grant_command("operator", "bob")
        self.assertEqual(buf.getvalue(), "Granted operator role to bob\n")
        m_client.return_value.grant_role.assert_called_once_with("operator", "bob")

    def test_role_grant_reports_forbidden(self):
        with (
            patch("slurm_quota.commands.ClientToken.load_value", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            m_client.return_value.grant_role.side_effect = ServiceHTTPError(403)
            role_grant_command("operator", "bob")
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            ["ERROR:slurm_quota:Access denied: admin role required to grant operator"],
        )

    def test_role_grant_reports_unreachable_service(self):
        with (
            patch("slurm_quota.commands.ClientToken.load_value", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            m_client.return_value.grant_role.side_effect = ServiceUnreachableError(
                "http://api:9911/", "boom"
            )
            role_grant_command("operator", "bob")
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(len(log_cm.output), 1)
        self.assertIn("Unable to reach slurm-quota REST API at", log_cm.output[0])

    def test_role_revoke_prints_confirmation(self):
        buf = io.StringIO()
        with (
            patch("slurm_quota.commands.ClientToken.load_value", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            redirect_stdout(buf),
        ):
            role_revoke_command("manager", "bob")
        self.assertEqual(buf.getvalue(), "Revoked manager role from bob\n")
        m_client.return_value.revoke_role.assert_called_once_with("manager", "bob")

    def test_role_revoke_reports_forbidden(self):
        with (
            patch("slurm_quota.commands.ClientToken.load_value", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            m_client.return_value.revoke_role.side_effect = ServiceHTTPError(403)
            role_revoke_command("manager", "bob")
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            ["ERROR:slurm_quota:Access denied: admin role required to revoke manager"],
        )

    def test_role_revoke_reports_unreachable_service(self):
        with (
            patch("slurm_quota.commands.ClientToken.load_value", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            m_client.return_value.revoke_role.side_effect = ServiceUnreachableError(
                "http://api:9911/", "boom"
            )
            role_revoke_command("manager", "bob")
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(len(log_cm.output), 1)
        self.assertIn("Unable to reach slurm-quota REST API at", log_cm.output[0])

    def test_role_managers_list_prints_accounts(self):
        buf = io.StringIO()
        with (
            patch("slurm_quota.commands.ClientToken.load_value", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            redirect_stdout(buf),
        ):
            m_client.return_value.list_manager_accounts.return_value = ["dev", "hpc"]
            role_managers_list_command("bob")
        self.assertEqual(buf.getvalue(), "dev\nhpc\n")
        m_client.return_value.list_manager_accounts.assert_called_once_with("bob")

    def test_role_managers_list_prints_empty_message(self):
        buf = io.StringIO()
        with (
            patch("slurm_quota.commands.ClientToken.load_value", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            redirect_stdout(buf),
        ):
            m_client.return_value.list_manager_accounts.return_value = []
            role_managers_list_command("bob")
        self.assertEqual(buf.getvalue(), "No accounts assigned to manager bob\n")

    def test_role_managers_list_reports_forbidden(self):
        with (
            patch("slurm_quota.commands.ClientToken.load_value", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            m_client.return_value.list_manager_accounts.side_effect = ServiceHTTPError(
                403
            )
            role_managers_list_command("bob")
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:Access denied: admin role required to list manager accounts"
            ],
        )

    def test_role_managers_list_reports_unreachable_service(self):
        with (
            patch("slurm_quota.commands.ClientToken.load_value", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            m_client.return_value.list_manager_accounts.side_effect = (
                ServiceUnreachableError("http://api:9911/", "boom")
            )
            role_managers_list_command("bob")
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(len(log_cm.output), 1)
        self.assertIn("Unable to reach slurm-quota REST API at", log_cm.output[0])

    def test_role_managers_add_prints_confirmation(self):
        buf = io.StringIO()
        with (
            patch("slurm_quota.commands.ClientToken.load_value", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            redirect_stdout(buf),
        ):
            role_managers_add_command("bob", "hpc")
        self.assertEqual(buf.getvalue(), "Assigned account hpc to manager bob\n")
        m_client.return_value.add_manager_account.assert_called_once_with("bob", "hpc")

    def test_role_managers_add_reports_forbidden(self):
        with (
            patch("slurm_quota.commands.ClientToken.load_value", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            m_client.return_value.add_manager_account.side_effect = ServiceHTTPError(
                403
            )
            role_managers_add_command("bob", "hpc")
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:Access denied: admin role required to assign manager accounts"
            ],
        )

    def test_role_managers_add_reports_unreachable_service(self):
        with (
            patch("slurm_quota.commands.ClientToken.load_value", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            m_client.return_value.add_manager_account.side_effect = (
                ServiceUnreachableError("http://api:9911/", "boom")
            )
            role_managers_add_command("bob", "hpc")
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(len(log_cm.output), 1)
        self.assertIn("Unable to reach slurm-quota REST API at", log_cm.output[0])

    def test_role_managers_remove_prints_confirmation(self):
        buf = io.StringIO()
        with (
            patch("slurm_quota.commands.ClientToken.load_value", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            redirect_stdout(buf),
        ):
            role_managers_remove_command("bob", "hpc")
        self.assertEqual(buf.getvalue(), "Removed account hpc from manager bob\n")
        m_client.return_value.remove_manager_account.assert_called_once_with(
            "bob", "hpc"
        )

    def test_role_managers_remove_reports_forbidden(self):
        with (
            patch("slurm_quota.commands.ClientToken.load_value", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            m_client.return_value.remove_manager_account.side_effect = ServiceHTTPError(
                403
            )
            role_managers_remove_command("bob", "hpc")
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            log_cm.output,
            [
                "ERROR:slurm_quota:Access denied: admin role required to remove manager accounts"
            ],
        )

    def test_role_managers_remove_reports_unreachable_service(self):
        with (
            patch("slurm_quota.commands.ClientToken.load_value", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            m_client.return_value.remove_manager_account.side_effect = (
                ServiceUnreachableError("http://api:9911/", "boom")
            )
            role_managers_remove_command("bob", "hpc")
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(len(log_cm.output), 1)
        self.assertIn("Unable to reach slurm-quota REST API at", log_cm.output[0])


class TestTokenCommand(SlurmQuotaTestCase):
    def test_token_show_prints_metadata_from_saved_file(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env({"XDG_CONFIG_HOME": str(config_home)})
        token = craft_jwt(login="alice", exp=4_102_444_800)

        ClientToken(token, "").save()
        buf = io.StringIO()
        with (
            patch.object(
                TokenPayload,
                "expiry",
                return_value="2099-12-31 23:59:59 UTC",
            ),
            redirect_stdout(buf),
        ):
            token_command()
        self.assertEqual(
            buf.getvalue(),
            dedent_lines(
                f"Source: {ClientToken.path()}",
                "Username: alice",
                "Type: user",
                "Scopes: all scopes of user role",
                "Expires: 2099-12-31 23:59:59 UTC",
            ),
        )

    def test_token_show_exits_when_no_token(self):
        config_home = Path(self._tmp.name) / "empty-config"
        self.env({"XDG_CONFIG_HOME": str(config_home)})
        with (
            self.assertLogs("slurm_quota", level="INFO") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            token_command()
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("No API token available", log_cm.output[0])
        self.assertIn(RELOGIN_GUIDANCE, log_cm.output[1])

    def test_token_save_writes_env_token(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env(
            {
                "XDG_CONFIG_HOME": str(config_home),
                "SLURM_QUOTA_TOKEN": craft_jwt(),
            }
        )

        buf = io.StringIO()
        with redirect_stdout(buf):
            token_command(save=True)
        token_path = ClientToken.path()
        self.assertTrue(token_path.is_file())
        self.assertEqual(
            buf.getvalue(),
            f"Authentication token saved to {token_path}\n",
        )

    def test_token_save_requires_env_token(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env({"XDG_CONFIG_HOME": str(config_home)})
        with (
            patch.dict("os.environ", {"SLURM_QUOTA_TOKEN": ""}, clear=False),
            self.assertRaises(SystemExit) as cm,
        ):
            token_command(save=True)
        self.assertEqual(cm.exception.code, 1)


class TestPruneCommand(SlurmQuotaTestCase):
    def _prune_targets(self, **kwargs):
        with (
            patch("slurm_quota.commands.auth.get_current_user", return_value="root"),
            patch(
                "slurm_quota.commands.prune_resources",
                return_value={"preallocs": 0, "users": 0, "accounts": 0},
            ) as prune_mock,
            redirect_stdout(io.StringIO()),
        ):
            prune_command(**kwargs)
        return prune_mock.call_args.args[0]

    def test_prune_no_selector_selects_all_targets(self):
        self.assertEqual(self._prune_targets(), {"preallocs", "users", "accounts"})

    def test_prune_all_selects_all_targets(self):
        self.assertEqual(
            self._prune_targets(all_targets=True), {"preallocs", "users", "accounts"}
        )

    def test_prune_filter_without_matching_selector_is_rejected(self):
        for selector, filters in (
            ({}, {"user_filter": "u1"}),
            ({}, {"account_filter": "a1"}),
            ({}, {"user_filter": "u1", "account_filter": "a1"}),
            ({"all_targets": True}, {"user_filter": "u1"}),
            ({"all_targets": True}, {"account_filter": "a1"}),
            ({"users": True}, {"account_filter": "a1"}),
            ({"users": True}, {"user_filter": "u1", "account_filter": "a1"}),
            ({"accounts": True}, {"user_filter": "u1"}),
            ({"accounts": True}, {"user_filter": "u1", "account_filter": "a1"}),
        ):
            with self.subTest(**selector, **filters):
                with (
                    patch(
                        "slurm_quota.commands.auth.get_current_user",
                        return_value="root",
                    ),
                    patch("slurm_quota.commands.prune_resources") as prune_mock,
                    self.assertLogs("slurm_quota", level="ERROR") as log_cm,
                    self.assertRaises(SystemExit) as cm,
                ):
                    prune_command(**selector, **filters)
                self.assertEqual(cm.exception.code, 1)
                prune_mock.assert_not_called()
                self.assertIn("filter requires", log_cm.output[0])

    def test_prune_explicit_selector_with_filter_keeps_selector(self):
        self.assertEqual(
            self._prune_targets(preallocs=True, user_filter="u1", account_filter="a1"),
            {"preallocs"},
        )
        self.assertEqual(self._prune_targets(users=True, user_filter="u1"), {"users"})
        self.assertEqual(
            self._prune_targets(accounts=True, account_filter="a1"), {"accounts"}
        )
