"""Unit tests for slurm_quota.timestamps."""

from __future__ import annotations

from slurm_quota.timestamps import format_timestamp_with_timezone
from tests.test_support import SlurmQuotaTestCase, local_timezone


class TestFormatTimestampWithTimezone(SlurmQuotaTestCase):
    def test_local_timezone(self):
        with local_timezone("Europe/Paris"):
            self.assertEqual(
                format_timestamp_with_timezone("2026-11-23 16:43:09"),
                "2026-11-23 17:43:09 CET",
            )
            self.assertEqual(
                format_timestamp_with_timezone("2024-06-01T10:00:00"),
                "2024-06-01 12:00:00 CEST",
            )

    def test_utc(self):
        with local_timezone("UTC"):
            self.assertEqual(
                format_timestamp_with_timezone("2024-01-15T12:30:45"),
                "2024-01-15 12:30:45 UTC",
            )

    def test_missing_or_invalid(self):
        self.assertEqual(format_timestamp_with_timezone(None), "N/A")
        self.assertEqual(format_timestamp_with_timezone(""), "N/A")
        self.assertEqual(format_timestamp_with_timezone("invalid"), "N/A")
