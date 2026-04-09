"""Unit tests for show_user_stats."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from unittest.mock import patch

from urllib.error import URLError

from tests.test_support import SlurmQuotaTestCase
from tests.testing_utils import dedent_lines


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
    def _run_show(self, *args, **kwargs):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.sq.show_user_stats(*args, **kwargs)
        return buf.getvalue()

    def test_no_data_for_explicit_username(self):
        with patch.object(
            self.sq,
            "fetch_stats_from_service",
            return_value=([], []),
        ):
            out = self._run_show("bob", False)
        expected = dedent_lines("No data found for user: bob")
        self.assertEqual(out, expected)

    def test_no_users_when_show_all_empty(self):
        with patch.object(
            self.sq,
            "fetch_stats_from_service",
            return_value=([], []),
        ):
            out = self._run_show(None, True)
        expected = dedent_lines("No users found in service")
        self.assertEqual(out, expected)

    def test_prints_user_and_account_tables(self):
        self.env({"NO_COLOR": "1"})
        users, accounts = _sample_users_and_accounts()
        with (
            patch.object(
                self.sq,
                "fetch_stats_from_service",
                return_value=(users, accounts),
            ),
            patch.object(
                self.sq,
                "format_timestamp_with_timezone",
                return_value="TS_FIXED",
            ),
        ):
            out = self._run_show("alice", False)
        expected = dedent_lines(
            "                          |                                CPU                                 |                                GPU                                 |",
            "USER                      |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
            "----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
            "alice                     |         120           60(2)      600 [██████░░░░░░░░░░░░░░]  30.0% |           0            0(2)        ∞                               | TS_FIXED                 ",
            "",
            "                          |                                CPU                                 |                                GPU                                 |",
            "ACCOUNT                   |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
            "----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
            "acct1                     |           1            0(0)        ∞                               |           0            0(0)        ∞                               | TS_FIXED                 ",
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
        with (
            patch.object(
                self.sq,
                "fetch_stats_from_service",
                return_value=([user], []),
            ),
            patch.object(
                self.sq,
                "format_timestamp_with_timezone",
                return_value="TS_FIXED",
            ),
        ):
            out = self._run_show("u1", False, display_hours=True)
        expected = dedent_lines(
            "                          |                                CPU                                 |                                GPU                                 |",
            "USER                      |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
            "----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
            "u1                        |        2.00         0.00(0)        ∞                               |        0.00         0.00(0)        ∞                               | TS_FIXED                 ",
            "",
            "                          |                                CPU                                 |                                GPU                                 |",
            "ACCOUNT                   |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
            "----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
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
        with (
            patch.object(
                self.sq,
                "fetch_stats_from_service",
                return_value=([user], []),
            ),
            patch.object(
                self.sq,
                "format_timestamp_with_timezone",
                return_value="TS_FIXED",
            ),
        ):
            out = self._run_show("u1", False)
        expected = dedent_lines(
            "                          |                                CPU                                 |                                GPU                                 |",
            "USER                      |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
            "----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
            "u1                        |          50            0(0)      100 [██████████░░░░░░░░░░]  50.0% |           0            0(0)        ∞                               | TS_FIXED                 ",
            "",
            "                          |                                CPU                                 |                                GPU                                 |",
            "ACCOUNT                   |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
            "----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
        )
        self.assertEqual(out, expected)

    def test_uses_get_current_user_when_username_omitted(self):
        with (
            patch.object(self.sq, "get_current_user", return_value="carol") as m_gc,
            patch.object(
                self.sq,
                "fetch_stats_from_service",
                return_value=([], []),
            ) as m_fetch,
        ):
            out = self._run_show(None, False)
        m_gc.assert_called_once()
        m_fetch.assert_called_once_with("carol", False)
        self.assertEqual(out, dedent_lines("No users found in service"))

    def test_get_current_user_keyerror_exits(self):
        with patch.object(self.sq, "get_current_user", side_effect=KeyError):
            with self.assertRaises(SystemExit) as cm:
                self.sq.show_user_stats(None, False)
        self.assertEqual(cm.exception.code, 1)

    def test_stats_http_error_exits(self):
        with patch.object(
            self.sq,
            "fetch_stats_from_service",
            side_effect=self.sq.StatsHTTPError(502),
        ):
            with self.assertRaises(SystemExit) as cm:
                self.sq.show_user_stats("x", False)
        self.assertEqual(cm.exception.code, 1)

    def test_urlerror_exits(self):
        with patch.object(
            self.sq,
            "fetch_stats_from_service",
            side_effect=URLError("down"),
        ):
            with self.assertRaises(SystemExit) as cm:
                self.sq.show_user_stats("x", False)
        self.assertEqual(cm.exception.code, 1)

    def test_generic_exception_exits(self):
        with patch.object(
            self.sq,
            "fetch_stats_from_service",
            side_effect=ValueError("bad json"),
        ):
            with self.assertRaises(SystemExit) as cm:
                self.sq.show_user_stats("x", False)
        self.assertEqual(cm.exception.code, 1)
