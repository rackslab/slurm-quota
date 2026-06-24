"""Unit tests for GET /stats."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

from slurm_quota.database import (
    grant_manager,
    grant_manager_account,
    grant_operator,
    init_database,
)

from tests.unit.serve.routes.base import ServeRoutesTestCase, app


class TestStatsRoute(ServeRoutesTestCase):
    def test_stats_returns_users_and_accounts(self):
        init_database()
        client = app.test_client()
        resp = client.get("/stats", headers=self._headers())
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        self.assertIn("users", body)
        self.assertIn("accounts", body)

    def test_stats_supports_username_query_filter(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("alice", 120),
            )
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("bob", 30),
            )
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("hpc", 100),
            )
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("dev", 40),
            )
            conn.commit()

        with patch("slurm_quota.slurm.get_user_accounts", return_value={"hpc"}):
            client = app.test_client()
            resp = client.get("/stats?username=alice", headers=self._headers())

        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        self.assertEqual(len(body["users"]), 1)
        self.assertEqual(body["users"][0]["username"], "alice")
        self.assertEqual(len(body["accounts"]), 1)
        self.assertEqual(body["accounts"][0]["account"], "hpc")

    def test_stats_supports_account_query_filter(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("alice", 120),
            )
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("hpc", 100),
            )
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("dev", 40),
            )
            conn.commit()

        client = app.test_client()
        resp = client.get("/stats?account=hpc", headers=self._headers())
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        self.assertEqual(body["users"], [])
        self.assertEqual(len(body["accounts"]), 1)
        self.assertEqual(body["accounts"][0]["account"], "hpc")

    def test_stats_rejects_username_and_account_filters(self):
        init_database()
        client = app.test_client()
        resp = client.get(
            "/stats?username=alice&account=hpc",
            headers=self._headers(),
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            json.loads(resp.data),
            {
                "error": "bad_request",
                "message": "username and account are mutually exclusive",
            },
        )

    def test_stats_db_error_returns_500(self):
        init_database()
        with patch(
            "slurm_quota.serve.routes.query_users_aggregate",
            side_effect=sqlite3.Error("boom"),
        ):
            client = app.test_client()
            resp = client.get("/stats", headers=self._headers())
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(json.loads(resp.data), {"error": "db_error"})

    def test_stats_rejects_missing_token(self):
        init_database()
        client = app.test_client()
        resp = client.get("/stats")
        self.assertEqual(resp.status_code, 403)
        body = json.loads(resp.data)
        self.assertEqual(body["error"], "forbidden")

    def test_stats_rejects_invalid_token(self):
        init_database()
        client = app.test_client()
        resp = client.get(
            "/stats",
            headers={"Authorization": "Bearer invalid"},
        )
        self.assertEqual(resp.status_code, 401)
        body = json.loads(resp.data)
        self.assertEqual(body["error"], "unauthorized")

    def test_user_cannot_query_other_user_stats(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("bob", 30),
            )
            conn.commit()

        client = app.test_client()
        resp = client.get("/stats?username=bob", headers=self._headers("carol"))
        self.assertEqual(resp.status_code, 403)

    def test_operator_can_query_all_stats(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("bob", 30),
            )
            grant_operator(conn, "carol")
            conn.commit()

        client = app.test_client()
        resp = client.get("/stats?username=bob", headers=self._headers("carol"))
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        self.assertEqual(len(body["users"]), 1)
        self.assertEqual(body["users"][0]["username"], "bob")

    def test_user_scoped_stats_default(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("carol", 15),
            )
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("bob", 30),
            )
            conn.commit()

        client = app.test_client()
        resp = client.get("/stats", headers=self._headers("carol"))
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        self.assertEqual(len(body["users"]), 1)
        self.assertEqual(body["users"][0]["username"], "carol")

    def test_manager_can_query_assigned_account_stats(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("hpc", 100),
            )
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("dev", 40),
            )
            grant_manager(conn, "carol")
            grant_manager_account(conn, "carol", "hpc")
            conn.commit()

        client = app.test_client()
        resp = client.get("/stats?account=hpc", headers=self._headers("carol"))
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        self.assertEqual(len(body["accounts"]), 1)
        self.assertEqual(body["accounts"][0]["account"], "hpc")

    def test_manager_cannot_query_unassigned_account_stats(self):
        init_database()
        with self.db_connection() as conn:
            grant_manager(conn, "carol")
            grant_manager_account(conn, "carol", "hpc")
            conn.commit()

        client = app.test_client()
        resp = client.get("/stats?account=dev", headers=self._headers("carol"))
        self.assertEqual(resp.status_code, 403)

    def test_manager_can_query_member_user_stats(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("bob", 30),
            )
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("hpc", 100),
            )
            grant_manager(conn, "carol")
            grant_manager_account(conn, "carol", "hpc")
            conn.commit()

        with patch("slurm_quota.slurm.get_user_accounts", return_value={"hpc", "dev"}):
            client = app.test_client()
            resp = client.get("/stats?username=bob", headers=self._headers("carol"))

        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        self.assertEqual(len(body["users"]), 1)
        self.assertEqual(body["users"][0]["username"], "bob")
        self.assertEqual(len(body["accounts"]), 1)
        self.assertEqual(body["accounts"][0]["account"], "hpc")

    def test_manager_cannot_query_non_member_user_stats(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("bob", 30),
            )
            grant_manager(conn, "carol")
            grant_manager_account(conn, "carol", "hpc")
            conn.commit()

        with patch("slurm_quota.slurm.get_user_accounts", return_value={"dev"}):
            client = app.test_client()
            resp = client.get("/stats?username=bob", headers=self._headers("carol"))

        self.assertEqual(resp.status_code, 403)

    def test_manager_unfiltered_stats_returns_assigned_scope(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("bob", 30),
            )
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("dave", 20),
            )
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("hpc", 100),
            )
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("dev", 40),
            )
            grant_manager(conn, "carol")
            grant_manager_account(conn, "carol", "hpc")
            conn.commit()

        with patch(
            "slurm_quota.slurm.get_account_users",
            side_effect=lambda account: {"bob"} if account == "hpc" else set(),
        ):
            client = app.test_client()
            resp = client.get("/stats", headers=self._headers("carol"))

        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        usernames = {entry["username"] for entry in body["users"]}
        accounts = {entry["account"] for entry in body["accounts"]}
        self.assertEqual(usernames, {"bob"})
        self.assertEqual(accounts, {"hpc"})
