"""Unit tests for slurm_quota.serve."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

from slurm_quota import serve
from slurm_quota.database import init_database

from tests.test_support import SlurmQuotaTestCase


class TestServeAPI(SlurmQuotaTestCase):
    def test_health_returns_ok(self):
        client = serve.app.test_client()
        resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.data), {"status": "ok"})

    def test_stats_returns_users_and_accounts(self):
        init_database()
        client = serve.app.test_client()
        resp = client.get("/stats")
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
            client = serve.app.test_client()
            resp = client.get("/stats?username=alice")

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

        client = serve.app.test_client()
        resp = client.get("/stats?account=hpc")
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        self.assertEqual(body["users"], [])
        self.assertEqual(len(body["accounts"]), 1)
        self.assertEqual(body["accounts"][0]["account"], "hpc")

    def test_stats_rejects_username_and_account_filters(self):
        init_database()
        client = serve.app.test_client()
        resp = client.get("/stats?username=alice&account=hpc")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            json.loads(resp.data),
            {
                "error": "bad_request",
                "message": "username and account are mutually exclusive",
            },
        )

    def test_unknown_path_returns_not_found(self):
        client = serve.app.test_client()
        resp = client.get("/does-not-exist")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(json.loads(resp.data), {"error": "not_found"})

    def test_stats_db_error_returns_500(self):
        init_database()
        with patch(
            "slurm_quota.serve.sqlite3.connect",
            side_effect=sqlite3.Error("boom"),
        ):
            client = serve.app.test_client()
            resp = client.get("/stats")
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(json.loads(resp.data), {"error": "db_error"})
