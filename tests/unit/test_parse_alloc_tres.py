from __future__ import annotations

from tests.test_support import SlurmQuotaTestCase


class TestParseAllocTres(SlurmQuotaTestCase):
    def test_parse_alloc_tres(self):
        self.assertEqual(self.sq.parse_alloc_tres(""), {})
        self.assertEqual(
            self.sq.parse_alloc_tres("cpu=1,gres/gpu:h100=2,gres/gpu:h200=1"),
            {"h100": 2, "h200": 1},
        )
        self.assertEqual(
            self.sq.parse_alloc_tres("gres/gpu:a100=1,gres/gpu:a100=2"),
            {"a100": 3},
        )
