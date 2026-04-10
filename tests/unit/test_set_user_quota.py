"""Unit tests for ``set_user_quota``."""

from __future__ import annotations

from tests.test_support import SlurmQuotaTestCase


class TestSetUserQuota(SlurmQuotaTestCase):
    def test_insert_uses_default_gpu_from_settings(self):
        self.init_db()
        with self.db_connection() as conn:
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = ?",
                ("4242", "default_user_quota_gpu_minutes"),
            )
            conn.commit()
        self.sq.set_user_quota("fresh", 100)
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM users WHERE username=?",
                ("fresh",),
            ).fetchone()
        self.assertEqual(row, (100, 4242))

    def test_update_preserves_existing_gpu(self):
        self.init_db()
        self.sq.set_user_gpu_quota("u", 99)
        with self.db_connection() as conn:
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = ?",
                ("111", "default_user_quota_cpu_minutes"),
            )
            conn.commit()
        self.sq.set_user_quota("u", 500)
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM users WHERE username=?",
                ("u",),
            ).fetchone()
        self.assertEqual(row, (500, 99))
