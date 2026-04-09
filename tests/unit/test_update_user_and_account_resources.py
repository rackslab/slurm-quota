from __future__ import annotations

from tests.test_support import SlurmQuotaTestCase


class TestUpdateUserAndAccountResources(SlurmQuotaTestCase):
    def test_update_user_and_account_resources(self):
        self.init_db()
        status = self.sq.update_user_and_account_resources("u1", "a1", 10, None, 2)
        self.assertEqual(status, "none")
        with self.db_connect() as conn:
            conn.execute(
                "INSERT INTO jobs_preallocations (job_uuid, username, account, preallocated_cpu_minutes, array_size) VALUES (?, ?, ?, ?, ?)",
                ("uuid1", "u1", "a1", 5, 1),
            )
            conn.commit()
        status = self.sq.update_user_and_account_resources("u1", "a1", 10, "uuid1", 0)
        self.assertEqual(status, "removed")
