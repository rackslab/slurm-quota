"""Unit tests for /quotas/defaults endpoints."""

from __future__ import annotations

from slurm_quota.database import grant_operator, init_database

from tests.unit.serve.routes.base import ServeRoutesTestCase, app


class TestDefaultQuotasRoute(ServeRoutesTestCase):
    def _update_settings(self, **values: int) -> None:
        with self.db_connection() as conn:
            for key, value in values.items():
                conn.execute(
                    "UPDATE settings SET value = ? WHERE key = ?",
                    (str(value), key),
                )
            conn.commit()

    def test_admin_gets_default_quotas(self):
        init_database()
        self._update_settings(
            default_user_quota_cpu_minutes=100,
            default_user_quota_gpu_minutes=200,
            default_account_quota_cpu_minutes=300,
            default_account_quota_gpu_minutes=400,
        )
        client = app.test_client()
        resp = client.get("/quotas/defaults", headers=self._headers("alice"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.get_json(),
            {
                "user_cpu_minutes": 100,
                "user_gpu_minutes": 200,
                "account_cpu_minutes": 300,
                "account_gpu_minutes": 400,
            },
        )

    def test_manager_gets_default_quotas(self):
        init_database()
        with self.db_connection() as conn:
            grant_operator(conn, "carol")
            conn.commit()
        client = app.test_client()
        resp = client.get("/quotas/defaults", headers=self._headers("carol"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.get_json(),
            {
                "user_cpu_minutes": -1,
                "user_gpu_minutes": -1,
                "account_cpu_minutes": -1,
                "account_gpu_minutes": -1,
            },
        )

    def test_user_cannot_get_default_quotas(self):
        init_database()
        client = app.test_client()
        resp = client.get("/quotas/defaults", headers=self._headers("bob"))
        self.assertEqual(resp.status_code, 403)

    def test_admin_sets_all_default_quotas(self):
        init_database()
        client = app.test_client()
        with self.assertLogs("slurm_quota", level="INFO") as log_cm:
            resp = client.put(
                "/quotas/defaults",
                headers=self._headers("alice"),
                json={
                    "user_cpu_minutes": 10,
                    "user_gpu_minutes": 20,
                    "account_cpu_minutes": 30,
                    "account_gpu_minutes": 40,
                },
            )
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(
            log_cm.output,
            [
                "INFO:slurm_quota:default quotas: manager=alice updates="
                "{'user_cpu_minutes': 10, 'user_gpu_minutes': 20, "
                "'account_cpu_minutes': 30, 'account_gpu_minutes': 40}",
            ],
        )
        with self.db_connection() as conn:
            rows = dict(conn.execute("SELECT key, value FROM settings").fetchall())
        self.assertEqual(rows["default_user_quota_cpu_minutes"], "10")
        self.assertEqual(rows["default_user_quota_gpu_minutes"], "20")
        self.assertEqual(rows["default_account_quota_cpu_minutes"], "30")
        self.assertEqual(rows["default_account_quota_gpu_minutes"], "40")

    def test_manager_sets_partial_default_quotas(self):
        init_database()
        self._update_settings(
            default_user_quota_cpu_minutes=1,
            default_user_quota_gpu_minutes=2,
            default_account_quota_cpu_minutes=3,
            default_account_quota_gpu_minutes=4,
        )
        with self.db_connection() as conn:
            grant_operator(conn, "carol")
            conn.commit()
        client = app.test_client()
        resp = client.put(
            "/quotas/defaults",
            headers=self._headers("carol"),
            json={"user_cpu_minutes": 999},
        )
        self.assertEqual(resp.status_code, 204)
        with self.db_connection() as conn:
            rows = dict(conn.execute("SELECT key, value FROM settings").fetchall())
        self.assertEqual(rows["default_user_quota_cpu_minutes"], "999")
        self.assertEqual(rows["default_user_quota_gpu_minutes"], "2")
        self.assertEqual(rows["default_account_quota_cpu_minutes"], "3")
        self.assertEqual(rows["default_account_quota_gpu_minutes"], "4")

    def test_user_cannot_set_default_quotas(self):
        init_database()
        client = app.test_client()
        resp = client.put(
            "/quotas/defaults",
            headers=self._headers("bob"),
            json={"user_cpu_minutes": 100},
        )
        self.assertEqual(resp.status_code, 403)

    def test_put_requires_at_least_one_field(self):
        init_database()
        client = app.test_client()
        resp = client.put(
            "/quotas/defaults",
            headers=self._headers("alice"),
            json={},
        )
        self.assertEqual(resp.status_code, 400)

    def test_put_rejects_invalid_json_body(self):
        init_database()
        client = app.test_client()
        resp = client.put(
            "/quotas/defaults",
            headers=self._headers("alice"),
            data="not-json",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_put_rejects_value_below_minus_one(self):
        init_database()
        client = app.test_client()
        resp = client.put(
            "/quotas/defaults",
            headers=self._headers("alice"),
            json={"user_cpu_minutes": -2},
        )
        self.assertEqual(resp.status_code, 400)
