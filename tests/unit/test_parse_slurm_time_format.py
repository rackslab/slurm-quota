from __future__ import annotations

from tests.test_support import SlurmQuotaTestCase


class TestParseSlurmTimeFormat(SlurmQuotaTestCase):
    def test_parse_slurm_time_format(self):
        self.assertEqual(self.sq.parse_slurm_time_format(""), 0)
        self.assertEqual(self.sq.parse_slurm_time_format("  "), 0)
        self.assertIsNone(self.sq.parse_slurm_time_format("UNLIMITED"))
        self.assertEqual(self.sq.parse_slurm_time_format("1-02:30:00"), 1590)
        self.assertEqual(self.sq.parse_slurm_time_format("01:00:00"), 60)
        self.assertEqual(self.sq.parse_slurm_time_format("42"), 42)
        with self.assertRaises(ValueError):
            self.sq.parse_slurm_time_format("INVALID")
