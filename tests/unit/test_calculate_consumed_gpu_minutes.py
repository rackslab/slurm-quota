from __future__ import annotations

from tests.test_support import SlurmQuotaTestCase


class TestCalculateConsumedGpuMinutes(SlurmQuotaTestCase):
    def test_calculate_consumed_gpu_minutes(self):
        self.assertEqual(
            self.sq.calculate_consumed_gpu_minutes(
                {"h100": 2}, 30, {"__default__": 1.0}
            ),
            60,
        )
        self.assertEqual(
            self.sq.calculate_consumed_gpu_minutes(
                {"h100": 4}, 10, {"__default__": 1.0, "h100": 0.5}
            ),
            20,
        )
