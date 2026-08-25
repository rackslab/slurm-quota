"""Unit tests for PATCH /consumption (adjust) endpoints."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

from werkzeug.exceptions import NotFound

from slurm_quota.database import grant_operator, init_database
from tests.unit.serve.routes.base import ServeRoutesTestCase, app


class TestConsumptionRoutes(ServeRoutesTestCase):
    def test_admin_adjusts_user_cpu_consumption(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("bob", 100),
            )
            conn.commit()
        client = app.test_client()
        with self.assertLogs("slurm_quota", level="INFO") as log_cm:
            resp = client.patch(
                "/consumption/user/bob/cpu",
                headers=self._headers("alice"),
                json={"delta_minutes": 30},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"total_consumed_minutes": 130})
        self.assertEqual(
            log_cm.output,
            [
                "INFO:slurm_quota:consumption user cpu: manager=alice name=bob "
                "delta=+30 total=130 reason=n/a",
            ],
        )
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT total_consumed_cpu_minutes FROM users WHERE username = ?",
                ("bob",),
            ).fetchone()
        self.assertEqual(row[0], 130)

    def test_manager_adjusts_user_gpu_consumption(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_gpu_minutes) VALUES (?, ?)",
                ("bob", 50),
            )
            grant_operator(conn, "carol")
            conn.commit()
        client = app.test_client()
        with self.assertLogs("slurm_quota", level="INFO") as log_cm:
            resp = client.patch(
                "/consumption/user/bob/gpu",
                headers=self._headers("carol"),
                json={"delta_minutes": -20},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"total_consumed_minutes": 30})
        self.assertEqual(
            log_cm.output,
            [
                "INFO:slurm_quota:consumption user gpu: manager=carol name=bob "
                "delta=-20 total=30 reason=n/a",
            ],
        )

    def test_admin_adjusts_account_cpu_consumption(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("hpc", 200),
            )
            conn.commit()
        client = app.test_client()
        resp = client.patch(
            "/consumption/account/hpc/cpu",
            headers=self._headers("alice"),
            json={"delta_minutes": 10},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"total_consumed_minutes": 210})

    def test_manager_adjusts_account_gpu_consumption(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_gpu_minutes) VALUES (?, ?)",
                ("hpc", 90),
            )
            grant_operator(conn, "carol")
            conn.commit()
        client = app.test_client()
        resp = client.patch(
            "/consumption/account/hpc/gpu",
            headers=self._headers("carol"),
            json={"delta_minutes": 60},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"total_consumed_minutes": 150})

    def test_negative_delta_clamped_at_zero(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("bob", 30),
            )
            conn.commit()
        client = app.test_client()
        resp = client.patch(
            "/consumption/user/bob/cpu",
            headers=self._headers("alice"),
            json={"delta_minutes": -100},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"total_consumed_minutes": 0})

    def test_user_cannot_adjust_consumption(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("bob", 100),
            )
            conn.commit()
        self._request_expecting_abort(
            "PATCH",
            "/consumption/user/bob/cpu",
            403,
            description="Insufficient permissions",
            headers=self._headers("bob"),
            json={"delta_minutes": 10},
        )

    def test_missing_user_returns_400(self):
        init_database()
        self._request_expecting_abort(
            "PATCH",
            "/consumption/user/missing/cpu",
            400,
            description="User not found: missing",
            headers=self._headers("alice"),
            json={"delta_minutes": 10},
        )

    def test_invalid_username_returns_400(self):
        self._request_expecting_abort(
            "PATCH",
            "/consumption/user/bad name/cpu",
            400,
            description="Invalid username",
            headers=self._headers("alice"),
            json={"delta_minutes": 10},
        )

    def test_invalid_account_returns_400(self):
        self._request_expecting_abort(
            "PATCH",
            "/consumption/account/bad account/cpu",
            400,
            description="Invalid account",
            headers=self._headers("alice"),
            json={"delta_minutes": 10},
        )

    def test_admin_adjusts_user_cpu_consumption_with_at_sign(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("alice@site", 100),
            )
            conn.commit()
        client = app.test_client()
        resp = client.patch(
            "/consumption/user/alice@site/cpu",
            headers=self._headers("alice"),
            json={"delta_minutes": 30},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"total_consumed_minutes": 130})

    def test_admin_adjusts_account_cpu_consumption_with_at_sign(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("lab@site", 200),
            )
            conn.commit()
        client = app.test_client()
        resp = client.patch(
            "/consumption/account/lab@site/cpu",
            headers=self._headers("alice"),
            json={"delta_minutes": 10},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"total_consumed_minutes": 210})

    def test_invalid_body_returns_400(self):
        self._request_expecting_abort(
            "PATCH",
            "/consumption/user/bob/cpu",
            400,
            description="delta_minutes must be an integer",
            headers=self._headers("alice"),
            json={"delta_minutes": "30"},
        )

    def test_missing_delta_minutes_returns_400(self):
        self._request_expecting_abort(
            "PATCH",
            "/consumption/user/bob/cpu",
            400,
            description="delta_minutes must be an integer",
            headers=self._headers("alice"),
            json={},
        )

    def test_adjust_consumption_logs_reason_when_provided(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("bob", 100),
            )
            conn.commit()
        client = app.test_client()
        with self.assertLogs("slurm_quota", level="INFO") as log_cm:
            resp = client.patch(
                "/consumption/user/bob/cpu",
                headers=self._headers("alice"),
                json={"delta_minutes": 30, "reason": "correct billing error"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            log_cm.output,
            [
                "INFO:slurm_quota:consumption user cpu: manager=alice name=bob "
                "delta=+30 total=130 reason='correct billing error'",
            ],
        )

    def test_adjust_consumption_rejects_empty_reason(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("bob", 100),
            )
            conn.commit()
        resp = self._request_expecting_abort(
            "PATCH",
            "/consumption/user/bob/cpu",
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
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("bob", 100),
            )
            conn.commit()
        resp = self._request_expecting_abort(
            "PATCH",
            "/consumption/user/bob/cpu",
            400,
            description="reason must be a single line",
            headers=self._headers("alice"),
            json={"delta_minutes": 30, "reason": "line one\nline two"},
        )
        self.assertEqual(
            resp.get_json(),
            {"error": "bad_request", "message": "reason must be a single line"},
        )

    def test_unknown_entity_returns_404(self):
        self._request_expecting_abort(
            "PATCH",
            "/consumption/teams/bob/cpu",
            404,
            description=NotFound.description,
            headers=self._headers("alice"),
            json={"delta_minutes": 10},
        )

    def test_invalid_resource_returns_400(self):
        resp = self._request_expecting_abort(
            "PATCH",
            "/consumption/user/bob/mem",
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
                "/consumption/user/bob/cpu",
                headers=self._headers("alice"),
                json={"delta_minutes": 10},
            )
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.get_json(), {"error": "db_error"})
