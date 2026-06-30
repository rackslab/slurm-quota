"""Unit tests for slurm_quota.commands."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from unittest.mock import patch

from slurm_quota.client import ServiceHTTPError, ServiceUnreachableError
from slurm_quota.commands import (
    RELOGIN_GUIDANCE,
    create_status_bar,
    format_timestamp_with_timezone,
    role_grant_command,
    role_list_command,
    role_managers_add_command,
    role_managers_list_command,
    role_managers_remove_command,
    role_revoke_command,
    role_show_command,
    show_user_stats,
)
from tests.test_support import SlurmQuotaTestCase
from tests.testing_utils import dedent_lines


class TestFormatTimestampWithTimezone(SlurmQuotaTestCase):
    def test_format_timestamp_with_timezone(self):
        self.assertEqual(format_timestamp_with_timezone(""), "N/A")
        out = format_timestamp_with_timezone("2024-01-15T12:30:45")
        self.assertIn("2024", out)


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
            "total_consumed_cpu_minutes": 120,
            "total_preallocated_cpu_minutes": 60,
            "quota_cpu_minutes": 600,
            "total_consumed_gpu_minutes": 0,
            "total_preallocated_gpu_minutes": 0,
            "quota_gpu_minutes": -1,
        }
    ]
    accounts = [
        {
            "account": "acct1",
            "job_count": 0,
            "last_updated": None,
            "total_consumed_cpu_minutes": 1,
            "total_preallocated_cpu_minutes": 0,
            "quota_cpu_minutes": -1,
            "total_consumed_gpu_minutes": 0,
            "total_preallocated_gpu_minutes": 0,
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
        patcher = patch("slurm_quota.commands.APIClient")
        m_api = patcher.start()
        self.addCleanup(patcher.stop)
        if side_effect is not None:
            m_api.return_value.stats.side_effect = side_effect
        else:
            m_api.return_value.stats.return_value = return_value
        return m_api

    def test_no_data_for_explicit_username(self):
        self._mock_stats(return_value=([], []))
        out = self._run_show(username="bob", show_all=False)
        expected = dedent_lines("No data found for user: bob")
        self.assertEqual(out, expected)

    def test_no_data_for_explicit_account(self):
        self._mock_stats(return_value=([], []))
        out = self._run_show(account="projX", show_all=False)
        expected = dedent_lines("No data found for account: projX")
        self.assertEqual(out, expected)

    def test_no_users_when_show_all_empty(self):
        self._mock_stats(return_value=([], []))
        out = self._run_show(show_all=True)
        expected = dedent_lines("No users found in service")
        self.assertEqual(out, expected)

    def test_prints_user_and_account_tables(self):
        self.env({"NO_COLOR": "1"})
        users, accounts = _sample_users_and_accounts()
        self._mock_stats(return_value=(users, accounts))
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
        self._mock_stats(return_value=([], accounts))
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

    def test_display_hours_formats_numbers(self):
        self.env({"NO_COLOR": "1"})
        user = {
            "username": "u1",
            "job_count": 0,
            "last_updated": None,
            "total_consumed_cpu_minutes": 120,
            "total_preallocated_cpu_minutes": 0,
            "quota_cpu_minutes": -1,
            "total_consumed_gpu_minutes": 0,
            "total_preallocated_gpu_minutes": 0,
            "quota_gpu_minutes": -1,
        }
        self._mock_stats(return_value=([user], []))
        with patch(
            "slurm_quota.commands.format_timestamp_with_timezone",
            return_value="TS_FIXED",
        ):
            out = self._run_show(username="u1", show_all=False, display_hours=True)
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

    def test_finite_quota_shows_status_bar(self):
        self.env({"NO_COLOR": "1"})
        user = {
            "username": "u1",
            "job_count": 0,
            "last_updated": None,
            "total_consumed_cpu_minutes": 50,
            "total_preallocated_cpu_minutes": 0,
            "quota_cpu_minutes": 100,
            "total_consumed_gpu_minutes": 0,
            "total_preallocated_gpu_minutes": 0,
            "quota_gpu_minutes": -1,
        }
        self._mock_stats(return_value=([user], []))
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

    def test_first_column_cap_and_ellipsis_align_user_and_account_tables(self):
        self.env({"NO_COLOR": "1"})
        users = [
            {
                "username": "user_with_a_name_longer_than_thirty_chars",
                "job_count": 1,
                "last_updated": None,
                "total_consumed_cpu_minutes": 1,
                "total_preallocated_cpu_minutes": 1,
                "quota_cpu_minutes": -1,
                "total_consumed_gpu_minutes": 0,
                "total_preallocated_gpu_minutes": 0,
                "quota_gpu_minutes": -1,
            }
        ]
        accounts = [
            {
                "account": "account_with_a_name_longer_than_thirty_chars",
                "job_count": 1,
                "last_updated": None,
                "total_consumed_cpu_minutes": 1,
                "total_preallocated_cpu_minutes": 1,
                "quota_cpu_minutes": -1,
                "total_consumed_gpu_minutes": 0,
                "total_preallocated_gpu_minutes": 0,
                "quota_gpu_minutes": -1,
            }
        ]
        self._mock_stats(return_value=(users, accounts))
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
            m_api = self._mock_stats(return_value=([], []))
            out = self._run_show(show_all=False)
        m_gc.assert_called_once()
        m_api.return_value.stats.assert_called_once_with("carol", None, False)
        self.assertEqual(out, dedent_lines("No users found in service"))

    def test_skips_get_current_user_when_account_requested(self):
        with patch("slurm_quota.auth.get_current_user") as m_gc:
            m_api = self._mock_stats(return_value=([], []))
            out = self._run_show(account="hpc", show_all=False)
        m_gc.assert_not_called()
        m_api.return_value.stats.assert_called_once_with(None, "hpc", False)
        self.assertEqual(out, dedent_lines("No data found for account: hpc"))

    def test_get_current_user_keyerror_exits(self):
        with (
            patch("slurm_quota.auth.get_current_user", side_effect=KeyError),
            self.assertRaises(SystemExit) as cm,
        ):
            show_user_stats(show_all=False)
        self.assertEqual(cm.exception.code, 1)

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
        self._mock_stats(side_effect=ServiceUnreachableError("down"))
        with self.assertRaises(SystemExit) as cm:
            show_user_stats(username="x", show_all=False)
        self.assertEqual(cm.exception.code, 1)

    def test_generic_exception_exits(self):
        self._mock_stats(side_effect=ValueError("bad json"))
        with self.assertRaises(SystemExit) as cm:
            show_user_stats(username="x", show_all=False)
        self.assertEqual(cm.exception.code, 1)


class TestRoleCommands(SlurmQuotaTestCase):
    def test_role_show_prints_username_and_role(self):
        buf = io.StringIO()
        with (
            patch("slurm_quota.commands.load_service_token", return_value="token"),
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
            patch("slurm_quota.commands.load_service_token", return_value="token"),
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
            patch("slurm_quota.commands.load_service_token", return_value="token"),
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
            patch("slurm_quota.commands.load_service_token", return_value="token"),
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

    def test_role_list_prints_table(self):
        buf = io.StringIO()
        with (
            patch("slurm_quota.commands.load_service_token", return_value="token"),
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
            patch("slurm_quota.commands.load_service_token", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            redirect_stdout(buf),
        ):
            role_grant_command("operator", "bob")
        self.assertEqual(buf.getvalue(), "Granted operator role to bob\n")
        m_client.return_value.grant_role.assert_called_once_with("operator", "bob")

    def test_role_grant_reports_forbidden(self):
        with (
            patch("slurm_quota.commands.load_service_token", return_value="token"),
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
            patch("slurm_quota.commands.load_service_token", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            m_client.return_value.grant_role.side_effect = ServiceUnreachableError(
                "boom"
            )
            role_grant_command("operator", "bob")
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(len(log_cm.output), 1)
        self.assertIn("Failed to contact slurm-quota service:", log_cm.output[0])

    def test_role_revoke_prints_confirmation(self):
        buf = io.StringIO()
        with (
            patch("slurm_quota.commands.load_service_token", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            redirect_stdout(buf),
        ):
            role_revoke_command("manager", "bob")
        self.assertEqual(buf.getvalue(), "Revoked manager role from bob\n")
        m_client.return_value.revoke_role.assert_called_once_with("manager", "bob")

    def test_role_revoke_reports_forbidden(self):
        with (
            patch("slurm_quota.commands.load_service_token", return_value="token"),
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
            patch("slurm_quota.commands.load_service_token", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            m_client.return_value.revoke_role.side_effect = ServiceUnreachableError(
                "boom"
            )
            role_revoke_command("manager", "bob")
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(len(log_cm.output), 1)
        self.assertIn("Failed to contact slurm-quota service:", log_cm.output[0])

    def test_role_managers_list_prints_accounts(self):
        buf = io.StringIO()
        with (
            patch("slurm_quota.commands.load_service_token", return_value="token"),
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
            patch("slurm_quota.commands.load_service_token", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            redirect_stdout(buf),
        ):
            m_client.return_value.list_manager_accounts.return_value = []
            role_managers_list_command("bob")
        self.assertEqual(buf.getvalue(), "No accounts assigned to manager bob\n")

    def test_role_managers_list_reports_forbidden(self):
        with (
            patch("slurm_quota.commands.load_service_token", return_value="token"),
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
            patch("slurm_quota.commands.load_service_token", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            m_client.return_value.list_manager_accounts.side_effect = (
                ServiceUnreachableError("boom")
            )
            role_managers_list_command("bob")
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(len(log_cm.output), 1)
        self.assertIn("Failed to contact slurm-quota service:", log_cm.output[0])

    def test_role_managers_add_prints_confirmation(self):
        buf = io.StringIO()
        with (
            patch("slurm_quota.commands.load_service_token", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            redirect_stdout(buf),
        ):
            role_managers_add_command("bob", "hpc")
        self.assertEqual(buf.getvalue(), "Assigned account hpc to manager bob\n")
        m_client.return_value.add_manager_account.assert_called_once_with("bob", "hpc")

    def test_role_managers_add_reports_forbidden(self):
        with (
            patch("slurm_quota.commands.load_service_token", return_value="token"),
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
            patch("slurm_quota.commands.load_service_token", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            m_client.return_value.add_manager_account.side_effect = (
                ServiceUnreachableError("boom")
            )
            role_managers_add_command("bob", "hpc")
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(len(log_cm.output), 1)
        self.assertIn("Failed to contact slurm-quota service:", log_cm.output[0])

    def test_role_managers_remove_prints_confirmation(self):
        buf = io.StringIO()
        with (
            patch("slurm_quota.commands.load_service_token", return_value="token"),
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
            patch("slurm_quota.commands.load_service_token", return_value="token"),
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
            patch("slurm_quota.commands.load_service_token", return_value="token"),
            patch("slurm_quota.commands.APIClient") as m_client,
            self.assertLogs("slurm_quota", level="ERROR") as log_cm,
            self.assertRaises(SystemExit) as cm,
        ):
            m_client.return_value.remove_manager_account.side_effect = (
                ServiceUnreachableError("boom")
            )
            role_managers_remove_command("bob", "hpc")
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(len(log_cm.output), 1)
        self.assertIn("Failed to contact slurm-quota service:", log_cm.output[0])
