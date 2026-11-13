"""Unit tests for PATCH /consumption (adjust) endpoints."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

from werkzeug.exceptions import NotFound

from slurm_quota.database import grant_operator, init_database
from tests.unit.serve.routes.base import ServeRoutesTestCase, app


class TestConsumptionRoutes(ServeRoutesTestCase):
    def test_admin_adjusts_pair_cpu_consumption(self):
        init_database()
        with self.db_connection() as conn:
            self.insert_consumption(conn, "bob", "hpc", cpu_minutes=100)
            self.insert_consumption(conn, "bob", "dev", cpu_minutes=40)
            conn.commit()
        client = app.test_client()
        with self.assertLogs("slurm_quota", level="INFO") as log_cm:
            resp = client.patch(
                "/consumption/bob/hpc/cpu",
                headers=self._headers("alice"),
                json={"delta_minutes": 30},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"total_consumed_minutes": 130})
        self.assertEqual(
            log_cm.output,
            [
                "INFO:slurm_quota:consumption cpu: manager=alice user=bob "
                "account=hpc delta=+30 total=130 reason=n/a",
            ],
        )
        with self.db_connection() as conn:
            self.assertEqual(self.pair_consumption(conn, "bob", "hpc"), (7800, 0))
            self.assertEqual(self.pair_consumption(conn, "bob", "dev"), (2400, 0))

    def test_operator_adjusts_pair_gpu_consumption(self):
        init_database()
        with self.db_connection() as conn:
            self.insert_consumption(conn, "bob", "hpc", gpu_minutes=50)
            grant_operator(conn, "carol")
            conn.commit()
        client = app.test_client()
        with self.assertLogs("slurm_quota", level="INFO") as log_cm:
            resp = client.patch(
                "/consumption/bob/hpc/gpu",
                headers=self._headers("carol"),
                json={"delta_minutes": -20},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"total_consumed_minutes": 30})
        self.assertEqual(
            log_cm.output,
            [
                "INFO:slurm_quota:consumption gpu: manager=carol user=bob "
                "account=hpc delta=-20 total=30 reason=n/a",
            ],
        )

    def test_admin_adjusts_new_pair_of_existing_user_and_account(self):
        init_database()
        with self.db_connection() as conn:
            self.insert_consumption(conn, "bob", "hpc", cpu_minutes=10)
            self.insert_consumption(conn, "carol", "dev", cpu_minutes=10)
            conn.commit()
        client = app.test_client()
        resp = client.patch(
            "/consumption/bob/dev/cpu",
            headers=self._headers("alice"),
            json={"delta_minutes": 15},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"total_consumed_minutes": 15})

    def test_negative_delta_clamped_at_zero(self):
        init_database()
        with self.db_connection() as conn:
            self.insert_consumption(conn, "bob", "hpc", cpu_minutes=30)
            conn.commit()
        client = app.test_client()
        resp = client.patch(
            "/consumption/bob/hpc/cpu",
            headers=self._headers("alice"),
            json={"delta_minutes": -100},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"total_consumed_minutes": 0})

    def test_user_cannot_adjust_consumption(self):
        init_database()
        with self.db_connection() as conn:
            self.insert_consumption(conn, "bob", "hpc", cpu_minutes=100)
            conn.commit()
        self._request_expecting_abort(
            "PATCH",
            "/consumption/bob/hpc/cpu",
            403,
            description="Insufficient permissions",
            headers=self._headers("bob"),
            json={"delta_minutes": 10},
        )

    def test_missing_user_returns_400(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute("INSERT INTO accounts (account) VALUES ('hpc')")
            conn.commit()
        self._request_expecting_abort(
            "PATCH",
            "/consumption/missing/hpc/cpu",
            400,
            description="User not found: missing",
            headers=self._headers("alice"),
            json={"delta_minutes": 10},
        )

    def test_missing_account_returns_400(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute("INSERT INTO users (username) VALUES ('bob')")
            conn.commit()
        self._request_expecting_abort(
            "PATCH",
            "/consumption/bob/missing/cpu",
            400,
            description="Account not found: missing",
            headers=self._headers("alice"),
            json={"delta_minutes": 10},
        )

    def test_invalid_username_returns_400(self):
        self._request_expecting_abort(
            "PATCH",
            "/consumption/bad name/hpc/cpu",
            400,
            description="Invalid username",
            headers=self._headers("alice"),
            json={"delta_minutes": 10},
        )

    def test_invalid_account_returns_400(self):
        self._request_expecting_abort(
            "PATCH",
            "/consumption/bob/bad account/cpu",
            400,
            description="Invalid account",
            headers=self._headers("alice"),
            json={"delta_minutes": 10},
        )

    def test_admin_adjusts_consumption_with_at_sign(self):
        init_database()
        with self.db_connection() as conn:
            self.insert_consumption(conn, "alice@site", "lab@site", cpu_minutes=100)
            conn.commit()
        client = app.test_client()
        resp = client.patch(
            "/consumption/alice@site/lab@site/cpu",
            headers=self._headers("alice"),
            json={"delta_minutes": 30},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"total_consumed_minutes": 130})

    def test_invalid_body_returns_400(self):
        self._request_expecting_abort(
            "PATCH",
            "/consumption/bob/hpc/cpu",
            400,
            description="delta_minutes must be an integer",
            headers=self._headers("alice"),
            json={"delta_minutes": "30"},
        )

    def test_missing_delta_minutes_returns_400(self):
        self._request_expecting_abort(
            "PATCH",
            "/consumption/bob/hpc/cpu",
            400,
            description="delta_minutes must be an integer",
            headers=self._headers("alice"),
            json={},
        )

    def test_adjust_consumption_logs_reason_when_provided(self):
        init_database()
        with self.db_connection() as conn:
            self.insert_consumption(conn, "bob", "hpc", cpu_minutes=100)
            conn.commit()
        client = app.test_client()
        with self.assertLogs("slurm_quota", level="INFO") as log_cm:
            resp = client.patch(
                "/consumption/bob/hpc/cpu",
                headers=self._headers("alice"),
                json={"delta_minutes": 30, "reason": "correct billing error"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            log_cm.output,
            [
                "INFO:slurm_quota:consumption cpu: manager=alice user=bob "
                "account=hpc delta=+30 total=130 reason='correct billing error'",
            ],
        )

    def test_adjust_consumption_rejects_empty_reason(self):
        init_database()
        with self.db_connection() as conn:
            self.insert_consumption(conn, "bob", "hpc", cpu_minutes=100)
            conn.commit()
        resp = self._request_expecting_abort(
            "PATCH",
            "/consumption/bob/hpc/cpu",
            400,
            description="reason must not be empty",
            headers=self._headers("alice"),
            json={"delta_minutes": 30, "reason": ""},
        )
        self.assertEqual(
            resp.get_json(),
            {"error": "bad_request", "message": "reason must not be empty"},
        )

    def test_adjust_consumption_rejects_multiline_reason(self):
        init_database()
        with self.db_connection() as conn:
            self.insert_consumption(conn, "bob", "hpc", cpu_minutes=100)
            conn.commit()
        resp = self._request_expecting_abort(
            "PATCH",
            "/consumption/bob/hpc/cpu",
            400,
            description="reason must be a single line",
            headers=self._headers("alice"),
            json={"delta_minutes": 30, "reason": "line one\nline two"},
        )
        self.assertEqual(
            resp.get_json(),
            {"error": "bad_request", "message": "reason must be a single line"},
        )

    def test_missing_account_segment_returns_404(self):
        self._request_expecting_abort(
            "PATCH",
            "/consumption/bob/cpu",
            404,
            description=NotFound.description,
            headers=self._headers("alice"),
            json={"delta_minutes": 10},
        )

    def test_invalid_resource_returns_400(self):
        resp = self._request_expecting_abort(
            "PATCH",
            "/consumption/bob/hpc/mem",
            400,
            description="Invalid resource",
            headers=self._headers("alice"),
            json={"delta_minutes": 10},
        )
        self.assertEqual(
            resp.get_json(),
            {"error": "bad_request", "message": "Invalid resource"},
        )

    def test_db_error_returns_500(self):
        init_database()
        with patch(
            "slurm_quota.serve.routes.db_adjust_consumed_minutes",
            side_effect=sqlite3.Error("boom"),
        ):
            client = app.test_client()
            resp = client.patch(
                "/consumption/bob/hpc/cpu",
                headers=self._headers("alice"),
                json={"delta_minutes": 10},
            )
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.get_json(), {"error": "db_error"})
