"""Unit tests for slurm_quota.units."""

from __future__ import annotations

from slurm_quota.units import format_quota, format_seconds, usage_ratio
from tests.test_support import SlurmQuotaTestCase


class TestFormatSeconds(SlurmQuotaTestCase):
    def test_minutes_are_floored(self):
        self.assertEqual(format_seconds(0, False), "0")
        self.assertEqual(format_seconds(59, False), "0")
        self.assertEqual(format_seconds(90, False), "1")
        self.assertEqual(format_seconds(3600, False), "60")

    def test_hours_are_computed_from_seconds(self):
        self.assertEqual(format_seconds(5400, True), "1.50")
        # 1 hour 59 seconds: floored minutes (60) would give 1.00
        self.assertEqual(format_seconds(3659, True), "1.02")
        self.assertEqual(format_seconds(0, True), "0.00")


class TestFormatQuota(SlurmQuotaTestCase):
    def test_unlimited(self):
        self.assertEqual(format_quota(-1, False), "∞")
        self.assertEqual(format_quota(-1, True), "∞")

    def test_minutes_and_hours(self):
        self.assertEqual(format_quota(600, False), "600")
        self.assertEqual(format_quota(90, True), "1.50")
        self.assertEqual(format_quota(0, False), "0")


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
