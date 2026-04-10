from __future__ import annotations

from tests.test_support import SlurmQuotaTestCase


class TestUpdateUserAndAccountResources(SlurmQuotaTestCase):
    def test_new_user_and_account_get_default_quotas_from_settings(self):
        self.init_db()
        with self.db_connection() as conn:
            conn.executemany(
                "UPDATE settings SET value = ? WHERE key = ?",
                [
                    ("5000", "default_user_quota_cpu_minutes"),
                    ("600", "default_user_quota_gpu_minutes"),
                    ("80000", "default_account_quota_cpu_minutes"),
                    ("9000", "default_account_quota_gpu_minutes"),
                ],
            )
            conn.commit()
        self.sq.update_user_and_account_resources("newu", "newa", 1, None, 1)
        with self.db_connection() as conn:
            u = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM users WHERE username=?",
                ("newu",),
            ).fetchone()
            a = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM accounts WHERE account=?",
                ("newa",),
            ).fetchone()
        self.assertEqual(u, (5000, 600))
        self.assertEqual(a, (80000, 9000))

    def test_update_user_and_account_resources(self):
        self.init_db()
        status = self.sq.update_user_and_account_resources("u1", "a1", 10, None, 2)
        self.assertEqual(status, "none")
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO jobs_preallocations (job_uuid, username, account, preallocated_cpu_minutes, array_size) VALUES (?, ?, ?, ?, ?)",
                ("uuid1", "u1", "a1", 5, 1),
            )
            conn.commit()
        status = self.sq.update_user_and_account_resources("u1", "a1", 10, "uuid1", 0)
        self.assertEqual(status, "removed")
