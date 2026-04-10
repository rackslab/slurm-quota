"""Unit tests for ``set_account_gpu_quota``."""

from __future__ import annotations

from tests.test_support import SlurmQuotaTestCase


class TestSetAccountGpuQuota(SlurmQuotaTestCase):
    def test_insert_uses_default_cpu_from_settings(self):
        self.init_db()
        with self.db_connection() as conn:
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = ?",
                ("6666", "default_account_quota_cpu_minutes"),
            )
            conn.commit()
        self.sq.set_account_gpu_quota("freshag", 80)
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM accounts WHERE account=?",
                ("freshag",),
            ).fetchone()
        self.assertEqual(row, (6666, 80))

    def test_update_preserves_existing_cpu(self):
        self.init_db()
        self.sq.set_account_quota("b", 250)
        with self.db_connection() as conn:
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = ?",
                ("888", "default_account_quota_gpu_minutes"),
            )
            conn.commit()
        self.sq.set_account_gpu_quota("b", 44)
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM accounts WHERE account=?",
                ("b",),
            ).fetchone()
        self.assertEqual(row, (250, 44))
