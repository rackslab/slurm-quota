"""Unit tests for /factors/gpu endpoints."""

from __future__ import annotations

from slurm_quota.database import grant_operator, init_database

from tests.unit.serve.routes.base import ServeRoutesTestCase, app


class TestGpuFactorsRoute(ServeRoutesTestCase):
    def _seed_gpu_factors(self, **values: float) -> None:
        with self.db_connection() as conn:
            for gpu_type, factor in values.items():
                conn.execute(
                    "INSERT INTO gpu_factors (gpu_type, factor) VALUES (?, ?)",
                    (gpu_type, factor),
                )
            conn.commit()

    def test_admin_gets_gpu_factors(self):
        init_database()
        self._seed_gpu_factors(default=0.75, h100=0.5, a100=1.5)
        client = app.test_client()
        resp = client.get("/factors/gpu", headers=self._headers("alice"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.get_json(),
            {"default_factor": 0.75, "factors": {"h100": 0.5, "a100": 1.5}},
        )

    def test_manager_gets_gpu_factors(self):
        init_database()
        with self.db_connection() as conn:
            grant_operator(conn, "carol")
            conn.commit()
        client = app.test_client()
        resp = client.get("/factors/gpu", headers=self._headers("carol"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.get_json(),
            {"default_factor": 1.0, "factors": {}},
        )

    def test_user_cannot_get_gpu_factors(self):
        init_database()
        client = app.test_client()
        resp = client.get("/factors/gpu", headers=self._headers("bob"))
        self.assertEqual(resp.status_code, 403)

    def test_admin_sets_gpu_factor(self):
        init_database()
        client = app.test_client()
        with self.assertLogs("slurm_quota", level="INFO") as log_cm:
            resp = client.put(
                "/factors/gpu/h100",
                headers=self._headers("alice"),
                json={"factor": 0.25},
            )
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(
            log_cm.output,
            ["INFO:slurm_quota:gpu factor: manager=alice gpu_type=h100 factor=0.25"],
        )
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT factor FROM gpu_factors WHERE gpu_type = ?",
                ("h100",),
            ).fetchone()
        self.assertEqual(row[0], 0.25)

    def test_manager_sets_gpu_factor(self):
        init_database()
        with self.db_connection() as conn:
            grant_operator(conn, "carol")
            conn.commit()
        client = app.test_client()
        resp = client.put(
            "/factors/gpu/default",
            headers=self._headers("carol"),
            json={"factor": 2.0},
        )
        self.assertEqual(resp.status_code, 204)
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT factor FROM gpu_factors WHERE gpu_type = ?",
                ("default",),
            ).fetchone()
        self.assertEqual(row[0], 2.0)

    def test_user_cannot_set_gpu_factor(self):
        init_database()
        client = app.test_client()
        resp = client.put(
            "/factors/gpu/h100",
            headers=self._headers("bob"),
            json={"factor": 0.5},
        )
        self.assertEqual(resp.status_code, 403)

    def test_put_rejects_non_positive_factor(self):
        init_database()
        client = app.test_client()
        resp = client.put(
            "/factors/gpu/h100",
            headers=self._headers("alice"),
            json={"factor": 0},
        )
        self.assertEqual(resp.status_code, 400)

    def test_put_rejects_invalid_json_body(self):
        init_database()
        client = app.test_client()
        resp = client.put(
            "/factors/gpu/h100",
            headers=self._headers("alice"),
            data="not-json",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_put_requires_factor_field(self):
        init_database()
        client = app.test_client()
        resp = client.put(
            "/factors/gpu/h100",
            headers=self._headers("alice"),
            json={},
        )
        self.assertEqual(resp.status_code, 400)
