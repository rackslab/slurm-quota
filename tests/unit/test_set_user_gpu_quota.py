"""Unit tests for ``set_user_gpu_quota``."""

from __future__ import annotations

from tests.test_support import SlurmQuotaTestCase


class TestSetUserGpuQuota(SlurmQuotaTestCase):
    def test_insert_uses_default_cpu_from_settings(self):
        self.init_db()
        with self.db_connection() as conn:
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = ?",
                ("5151", "default_user_quota_cpu_minutes"),
            )
            conn.commit()
        self.sq.set_user_gpu_quota("freshg", 200)
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM users WHERE username=?",
                ("freshg",),
            ).fetchone()
        self.assertEqual(row, (5151, 200))

    def test_update_preserves_existing_cpu(self):
        self.init_db()
        self.sq.set_user_quota("v", 400)
        with self.db_connection() as conn:
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = ?",
                ("222", "default_user_quota_gpu_minutes"),
            )
            conn.commit()
        self.sq.set_user_gpu_quota("v", 77)
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM users WHERE username=?",
                ("v",),
            ).fetchone()
        self.assertEqual(row, (400, 77))
