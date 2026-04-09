from __future__ import annotations

from tests.test_support import SlurmQuotaTestCase


class TestFormatTimestampWithTimezone(SlurmQuotaTestCase):
    def test_format_timestamp_with_timezone(self):
        self.assertEqual(self.sq.format_timestamp_with_timezone(""), "N/A")
        out = self.sq.format_timestamp_with_timezone("2024-01-15T12:30:45")
        self.assertIn("2024", out)
