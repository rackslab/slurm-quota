from __future__ import annotations

from tests.test_support import SlurmQuotaTestCase


class TestCreateStatusBar(SlurmQuotaTestCase):
    def test_create_status_bar(self):
        self.assertEqual(self.sq.create_status_bar(0, 0), " " * 20)
        self.assertIn("50.0%", self.sq.create_status_bar(5, 10))
        self.assertIn("100.0%", self.sq.create_status_bar(15, 10))
