"""Unit tests for slurm_quota.web.dashboard."""

from __future__ import annotations

from slurm_quota.web.dashboard import decorate_breakdown, decorate_rows
from tests.test_support import SlurmQuotaTestCase


def _row(**overrides):
    row = {
        "username": "u1",
        "job_count": 1,
        "last_updated": "2024-06-01T10:00:00",
        "total_consumed_cpu_seconds": 0,
        "total_preallocated_cpu_seconds": 0,
        "quota_cpu_minutes": -1,
        "total_consumed_gpu_seconds": 0,
        "total_preallocated_gpu_seconds": 0,
        "quota_gpu_minutes": -1,
    }
    row.update(overrides)
    return row


class TestDecorateRows(SlurmQuotaTestCase):
    def test_usage_is_computed_in_seconds(self):
        # 9 min 59 s consumed + 30 s preallocated exceed the 10 min quota, while
        # displayed floored minutes (9 + 0) would only reach 90%.
        (row,) = decorate_rows(
            [
                _row(
                    total_consumed_cpu_seconds=599,
                    total_preallocated_cpu_seconds=30,
                    quota_cpu_minutes=10,
                )
            ],
            "username",
            display_unit="minutes",
        )
        self.assertEqual(row["cpu"]["consumed"], "9")
        self.assertEqual(row["cpu"]["preallocated"], "0")
        self.assertEqual(row["cpu"]["quota"], "10")
        self.assertEqual(row["cpu"]["quota_minutes"], 10)
        self.assertEqual(row["cpu"]["percent"], 100.0)
        self.assertEqual(row["cpu"]["status_class"], "bar-danger")

    def test_comment(self):
        rows = decorate_rows(
            [_row(username="u1", comment="PhD student"), _row(username="u2")],
            "username",
            display_unit="minutes",
        )
        self.assertEqual([row["comment"] for row in rows], ["PhD student", ""])

    def test_hours_and_unlimited_quota(self):
        (row,) = decorate_rows(
            [
                _row(
                    total_consumed_gpu_seconds=5400,
                    total_preallocated_gpu_seconds=1800,
                )
            ],
            "username",
            display_unit="hours",
        )
        self.assertEqual(row["gpu"]["consumed"], "1.50")
        self.assertEqual(row["gpu"]["preallocated"], "0.50")
        self.assertEqual(row["gpu"]["quota"], "∞")
        self.assertIsNone(row["gpu"]["percent"])
        self.assertEqual(row["gpu"]["status_class"], "bar-unlimited")

    def test_seconds(self):
        (row,) = decorate_rows(
            [
                _row(
                    total_consumed_cpu_seconds=599,
                    total_preallocated_cpu_seconds=30,
                    quota_cpu_minutes=10,
                )
            ],
            "username",
            display_unit="seconds",
        )
        self.assertEqual(row["cpu"]["consumed"], "599")
        self.assertEqual(row["cpu"]["preallocated"], "30")
        self.assertEqual(row["cpu"]["quota"], "600")
        self.assertEqual(row["cpu"]["quota_minutes"], 10)
        self.assertEqual(row["gpu"]["quota"], "∞")


class TestDecorateBreakdown(SlurmQuotaTestCase):
    def _entries(self):
        return decorate_breakdown(
            [
                {
                    "username": "alice",
                    "account": "hpc",
                    "consumed_cpu_seconds": 5400,
                    "consumed_gpu_seconds": 90,
                    "last_updated": "2024-06-01T10:00:00",
                },
                {
                    "username": "bob",
                    "account": "hpc",
                    "consumed_cpu_seconds": 60,
                    "consumed_gpu_seconds": 0,
                    "last_updated": None,
                },
            ],
            display_unit="minutes",
        )

    def test_formats_consumption_in_display_unit(self):
        alice, bob = self._entries()
        self.assertEqual(alice["username"], "alice")
        self.assertEqual(alice["account"], "hpc")
        self.assertEqual(alice["cpu"]["consumed"], "90")
        self.assertEqual(alice["gpu"]["consumed"], "1")
        self.assertEqual(alice["last_updated"], "2024-06-01T10:00:00")
        self.assertEqual(bob["last_updated"], "n/a")

    def test_user_rows_list_entries_by_account(self):
        rows = decorate_rows(
            [_row(username="alice"), _row(username="carol")],
            "username",
            display_unit="minutes",
            breakdown=self._entries(),
        )
        self.assertEqual([e["name"] for e in rows[0]["breakdown"]], ["hpc"])
        self.assertEqual(rows[1]["breakdown"], [])

    def test_account_rows_list_entries_by_user(self):
        (row,) = decorate_rows(
            [_row(username=None, account="hpc")],
            "account",
            display_unit="minutes",
            breakdown=self._entries(),
        )
        self.assertEqual([e["name"] for e in row["breakdown"]], ["alice", "bob"])

    def test_no_breakdown_by_default(self):
        (row,) = decorate_rows([_row()], "username", display_unit="minutes")
        self.assertEqual(row["breakdown"], [])
