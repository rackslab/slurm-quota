from __future__ import annotations

from tests.test_support import SlurmQuotaTestCase


class TestLoadGpuFactors(SlurmQuotaTestCase):
    def test_load_gpu_factors(self):
        factors = self.sq.load_gpu_factors()
        self.assertEqual(factors["__default__"], 1.0)
        self.init_db()
        with self.db_connect() as conn:
            conn.execute(
                "INSERT INTO gpu_factors (gpu_type, factor) VALUES (?, ?)",
                ("default", 1.25),
            )
            conn.execute(
                "INSERT INTO gpu_factors (gpu_type, factor) VALUES (?, ?)",
                ("h100", 2.5),
            )
            conn.commit()
        factors = self.sq.load_gpu_factors()
        self.assertEqual(factors["__default__"], 1.25)
        self.assertEqual(factors["h100"], 2.5)
