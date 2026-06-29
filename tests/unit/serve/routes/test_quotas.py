"""Unit tests for /quotas endpoints."""

from __future__ import annotations

from slurm_quota.database import grant_operator, init_database
from tests.unit.serve.routes.base import ServeRoutesTestCase, app


class TestQuotasRoute(ServeRoutesTestCase):
    def _update_settings(self, **values: int) -> None:
        with self.db_connection() as conn:
            for key, value in values.items():
                conn.execute(
                    "UPDATE settings SET value = ? WHERE key = ?",
                    (str(value), key),
                )
            conn.commit()

    def test_admin_sets_user_cpu_quota(self):
        init_database()
        client = app.test_client()
        with self.assertLogs("slurm_quota", level="INFO") as log_cm:
            resp = client.put(
                "/quotas/users/bob/cpu",
                headers=self._headers("alice"),
                json={"quota_minutes": 500},
            )
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(
            log_cm.output,
            [
                "INFO:slurm_quota:quota user cpu: manager=alice name=bob value=500",
            ],
        )
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes FROM users WHERE username = ?",
                ("bob",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 500)

    def test_manager_sets_user_gpu_quota(self):
        init_database()
        with self.db_connection() as conn:
            grant_operator(conn, "carol")
            conn.commit()
        client = app.test_client()
        with self.assertLogs("slurm_quota", level="INFO") as log_cm:
            resp = client.put(
                "/quotas/users/bob/gpu",
                headers=self._headers("carol"),
                json={"quota_minutes": 120},
            )
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(
            log_cm.output,
            [
                "INFO:slurm_quota:quota user gpu: manager=carol name=bob value=120",
            ],
        )
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_gpu_minutes FROM users WHERE username = ?",
                ("bob",),
            ).fetchone()
        self.assertEqual(row[0], 120)

    def test_admin_sets_account_cpu_quota(self):
        init_database()
        client = app.test_client()
        with self.assertLogs("slurm_quota", level="INFO") as log_cm:
            resp = client.put(
                "/quotas/accounts/hpc/cpu",
                headers=self._headers("alice"),
                json={"quota_minutes": -1},
            )
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(
            log_cm.output,
            [
                "INFO:slurm_quota:quota account cpu: manager=alice name=hpc value=-1",
            ],
        )
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes FROM accounts WHERE account = ?",
                ("hpc",),
            ).fetchone()
        self.assertEqual(row[0], -1)

    def test_manager_sets_account_gpu_quota(self):
        init_database()
        with self.db_connection() as conn:
            grant_operator(conn, "carol")
            conn.commit()
        client = app.test_client()
        with self.assertLogs("slurm_quota", level="INFO") as log_cm:
            resp = client.put(
                "/quotas/accounts/hpc/gpu",
                headers=self._headers("carol"),
                json={"quota_minutes": 900},
            )
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(
            log_cm.output,
            [
                "INFO:slurm_quota:quota account gpu: manager=carol name=hpc value=900",
            ],
        )
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_gpu_minutes FROM accounts WHERE account = ?",
                ("hpc",),
            ).fetchone()
        self.assertEqual(row[0], 900)

    def test_user_cannot_set_quota(self):
        init_database()
        client = app.test_client()
        resp = client.put(
            "/quotas/users/bob/cpu",
            headers=self._headers("bob"),
            json={"quota_minutes": 100},
        )
        self.assertEqual(resp.status_code, 403)

    def test_invalid_username_returns_400(self):
        client = app.test_client()
        resp = client.put(
            "/quotas/users/bad name/cpu",
            headers=self._headers("alice"),
            json={"quota_minutes": 100},
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_account_returns_400(self):
        client = app.test_client()
        resp = client.put(
            "/quotas/accounts/bad account/cpu",
            headers=self._headers("alice"),
            json={"quota_minutes": 100},
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_body_returns_400(self):
        client = app.test_client()
        resp = client.put(
            "/quotas/users/bob/cpu",
            headers=self._headers("alice"),
            json={"quota_minutes": -2},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.get_json(),
            {
                "error": "bad_request",
                "message": "quota_minutes must be an integer >= -1",
            },
        )

    def test_missing_quota_minutes_returns_400(self):
        client = app.test_client()
        resp = client.put(
            "/quotas/users/bob/cpu",
            headers=self._headers("alice"),
            json={},
        )
        self.assertEqual(resp.status_code, 400)

    def test_user_cpu_quota_applies_default_gpu_on_create(self):
        self._update_settings(default_user_quota_gpu_minutes=4242)
        client = app.test_client()
        resp = client.put(
            "/quotas/users/marcus/cpu",
            headers=self._headers("alice"),
            json={"quota_minutes": 500},
        )
        self.assertEqual(resp.status_code, 204)
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM users WHERE username = ?",
                ("marcus",),
            ).fetchone()
        self.assertEqual(row, (500, 4242))

    def test_user_cpu_quota_preserves_gpu_on_update(self):
        with self.db_connection() as conn:
            conn.execute(
                """
                INSERT INTO users (username, quota_cpu_minutes, quota_gpu_minutes)
                VALUES (?, ?, ?)
                """,
                ("sofia", 10, 8888),
            )
            conn.commit()
        self._update_settings(default_user_quota_gpu_minutes=99999)
        client = app.test_client()
        resp = client.put(
            "/quotas/users/sofia/cpu",
            headers=self._headers("alice"),
            json={"quota_minutes": 500},
        )
        self.assertEqual(resp.status_code, 204)
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM users WHERE username = ?",
                ("sofia",),
            ).fetchone()
        self.assertEqual(row, (500, 8888))

    def test_user_gpu_quota_applies_default_cpu_on_create(self):
        self._update_settings(default_user_quota_cpu_minutes=5151)
        client = app.test_client()
        resp = client.put(
            "/quotas/users/liam/gpu",
            headers=self._headers("alice"),
            json={"quota_minutes": 100},
        )
        self.assertEqual(resp.status_code, 204)
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM users WHERE username = ?",
                ("liam",),
            ).fetchone()
        self.assertEqual(row, (5151, 100))

    def test_user_gpu_quota_preserves_cpu_on_update(self):
        with self.db_connection() as conn:
            conn.execute(
                """
                INSERT INTO users (username, quota_cpu_minutes, quota_gpu_minutes)
                VALUES (?, ?, ?)
                """,
                ("noah", 3333, 20),
            )
            conn.commit()
        self._update_settings(default_user_quota_cpu_minutes=77777)
        client = app.test_client()
        resp = client.put(
            "/quotas/users/noah/gpu",
            headers=self._headers("alice"),
            json={"quota_minutes": 100},
        )
        self.assertEqual(resp.status_code, 204)
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM users WHERE username = ?",
                ("noah",),
            ).fetchone()
        self.assertEqual(row, (3333, 100))

    def test_account_cpu_quota_applies_default_gpu_on_create(self):
        self._update_settings(default_account_quota_gpu_minutes=3333)
        client = app.test_client()
        resp = client.put(
            "/quotas/accounts/molecular_dynamics/cpu",
            headers=self._headers("alice"),
            json={"quota_minutes": 50},
        )
        self.assertEqual(resp.status_code, 204)
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM accounts WHERE account = ?",
                ("molecular_dynamics",),
            ).fetchone()
        self.assertEqual(row, (50, 3333))

    def test_account_cpu_quota_preserves_gpu_on_update(self):
        with self.db_connection() as conn:
            conn.execute(
                """
                INSERT INTO accounts (account, quota_cpu_minutes, quota_gpu_minutes)
                VALUES (?, ?, ?)
                """,
                ("oceanography", 100, 4444),
            )
            conn.commit()
        self._update_settings(default_account_quota_gpu_minutes=88888)
        client = app.test_client()
        resp = client.put(
            "/quotas/accounts/oceanography/cpu",
            headers=self._headers("alice"),
            json={"quota_minutes": -1},
        )
        self.assertEqual(resp.status_code, 204)
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM accounts WHERE account = ?",
                ("oceanography",),
            ).fetchone()
        self.assertEqual(row, (-1, 4444))

    def test_account_gpu_quota_applies_default_cpu_on_create(self):
        self._update_settings(default_account_quota_cpu_minutes=6666)
        client = app.test_client()
        resp = client.put(
            "/quotas/accounts/neuro_render/gpu",
            headers=self._headers("alice"),
            json={"quota_minutes": 80},
        )
        self.assertEqual(resp.status_code, 204)
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM accounts WHERE account = ?",
                ("neuro_render",),
            ).fetchone()
        self.assertEqual(row, (6666, 80))

    def test_account_gpu_quota_preserves_cpu_on_update(self):
        with self.db_connection() as conn:
            conn.execute(
                """
                INSERT INTO accounts (account, quota_cpu_minutes, quota_gpu_minutes)
                VALUES (?, ?, ?)
                """,
                ("weather_cluster", 2222, 50),
            )
            conn.commit()
        self._update_settings(default_account_quota_cpu_minutes=55555)
        client = app.test_client()
        resp = client.put(
            "/quotas/accounts/weather_cluster/gpu",
            headers=self._headers("alice"),
            json={"quota_minutes": 200},
        )
        self.assertEqual(resp.status_code, 204)
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes, quota_gpu_minutes FROM accounts WHERE account = ?",
                ("weather_cluster",),
            ).fetchone()
        self.assertEqual(row, (2222, 200))
