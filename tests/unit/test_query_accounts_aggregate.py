from __future__ import annotations

from tests.test_support import SlurmQuotaTestCase


class TestQueryAccountsAggregate(SlurmQuotaTestCase):
    def test_query_accounts_aggregate(self):
        self.init_db()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("u1", 10),
            )
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("a1", 20),
            )
            conn.execute(
                "INSERT INTO jobs_preallocations (job_uuid, username, account, preallocated_cpu_minutes, array_size) VALUES (?, ?, ?, ?, ?)",
                ("j1", "u1", "a1", 5, 2),
            )
            conn.commit()
            accounts = self.sq.query_accounts_aggregate(conn, {"a1"})
        self.assertEqual(accounts[0]["account"], "a1")
