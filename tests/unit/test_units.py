"""Unit tests for slurm_quota.units."""

from __future__ import annotations

from slurm_quota.units import (
    HOURS,
    MINUTES,
    SECONDS,
    UNITS,
    format_quota,
    format_seconds,
    usage_ratio,
)
from tests.test_support import SlurmQuotaTestCase


class TestFormatSeconds(SlurmQuotaTestCase):
    def test_minutes_are_floored(self):
        self.assertEqual(format_seconds(0, MINUTES), "0")
        self.assertEqual(format_seconds(59, MINUTES), "0")
        self.assertEqual(format_seconds(90, MINUTES), "1")
        self.assertEqual(format_seconds(3600, MINUTES), "60")

    def test_seconds_are_unchanged(self):
        self.assertEqual(format_seconds(0, SECONDS), "0")
        self.assertEqual(format_seconds(59, SECONDS), "59")
        self.assertEqual(format_seconds(3659, SECONDS), "3659")

    def test_hours_are_computed_from_seconds(self):
        self.assertEqual(format_seconds(5400, HOURS), "1.50")
        # 1 hour 59 seconds: floored minutes (60) would give 1.00
        self.assertEqual(format_seconds(3659, HOURS), "1.02")
        self.assertEqual(format_seconds(0, HOURS), "0.00")


class TestFormatQuota(SlurmQuotaTestCase):
    def test_unlimited(self):
        for unit in UNITS:
            self.assertEqual(format_quota(-1, unit), "∞")

    def test_seconds_minutes_and_hours(self):
        self.assertEqual(format_quota(600, MINUTES), "600")
        self.assertEqual(format_quota(90, HOURS), "1.50")
        self.assertEqual(format_quota(10, SECONDS), "600")
        self.assertEqual(format_quota(0, MINUTES), "0")


class TestUsageRatio(SlurmQuotaTestCase):
    def test_unlimited_or_zero_quota(self):
        self.assertIsNone(usage_ratio(100, -1))
        self.assertIsNone(usage_ratio(100, 0))

    def test_ratio_in_seconds(self):
        self.assertEqual(usage_ratio(0, 10), 0.0)
        self.assertEqual(usage_ratio(300, 10), 0.5)
        # 9 min 59 s: floored minutes would give 0.9
        self.assertAlmostEqual(usage_ratio(599, 10), 599 / 600)

    def test_ratio_is_capped(self):
        self.assertEqual(usage_ratio(600, 10), 1.0)
        self.assertEqual(usage_ratio(1000, 10), 1.0)
