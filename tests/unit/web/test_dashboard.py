"""Unit tests for slurm_quota.web.dashboard."""

from __future__ import annotations

from slurm_quota.web.dashboard import decorate_rows
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
            display_hours=False,
        )
        self.assertEqual(row["cpu"]["consumed"], "9")
        self.assertEqual(row["cpu"]["preallocated"], "0")
        self.assertEqual(row["cpu"]["quota"], "10")
        self.assertEqual(row["cpu"]["quota_minutes"], 10)
        self.assertEqual(row["cpu"]["percent"], 100.0)
        self.assertEqual(row["cpu"]["status_class"], "bar-danger")

    def test_hours_and_unlimited_quota(self):
        (row,) = decorate_rows(
            [
                _row(
                    total_consumed_gpu_seconds=5400,
                    total_preallocated_gpu_seconds=1800,
                )
            ],
            "username",
            display_hours=True,
        )
        self.assertEqual(row["gpu"]["consumed"], "1.50")
        self.assertEqual(row["gpu"]["preallocated"], "0.50")
        self.assertEqual(row["gpu"]["quota"], "∞")
        self.assertIsNone(row["gpu"]["percent"])
        self.assertEqual(row["gpu"]["status_class"], "bar-unlimited")
