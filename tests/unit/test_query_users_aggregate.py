from __future__ import annotations

from tests.test_support import SlurmQuotaTestCase


class TestQueryUsersAggregate(SlurmQuotaTestCase):
    def test_query_users_aggregate(self):
        self.init_db()
        with self.db_connect() as conn:
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
            users = self.sq.query_users_aggregate(conn)
        self.assertEqual(users[0]["total_preallocated_cpu_minutes"], 10)
        self.assertEqual(users[0]["job_count"], 2)
