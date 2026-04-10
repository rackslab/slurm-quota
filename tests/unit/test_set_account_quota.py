"""Unit tests for ``set_account_quota``."""

from __future__ import annotations

from tests.test_support import SlurmQuotaTestCase


class TestSetAccountQuota(SlurmQuotaTestCase):
    def test_insert_uses_default_gpu_from_settings(self):
        self.init_db()
        with self.db_connection() as conn:
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = ?",
                ("3333", "default_account_quota_gpu_minutes"),
            )
            conn.commit()
        self.sq.set_account_quota("fresha", 50)
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM accounts WHERE account=?",
                ("fresha",),
            ).fetchone()
        self.assertEqual(row, (50, 3333))

    def test_update_preserves_existing_gpu(self):
        self.init_db()
        self.sq.set_account_gpu_quota("a", 88)
        with self.db_connection() as conn:
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = ?",
                ("999", "default_account_quota_cpu_minutes"),
            )
            conn.commit()
        self.sq.set_account_quota("a", 300)
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM accounts WHERE account=?",
                ("a",),
            ).fetchone()
        self.assertEqual(row, (300, 88))
