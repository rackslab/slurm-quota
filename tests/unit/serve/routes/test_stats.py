"""Unit tests for GET /stats and the visibility of stats."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

from slurm_quota.database import (
    grant_manager,
    grant_manager_account,
    grant_operator,
    init_database,
    update_user_and_account_resources,
)
from slurm_quota.serve.routes import StatsVisibility
from tests.test_support import SlurmQuotaTestCase
from tests.unit.serve.routes.base import ServeRoutesTestCase, app
from tests.unit.serve.support import issue_test_token


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
                "INSERT INTO users (username) VALUES (?)",
                ("alice",),
            )
            conn.execute(
                "INSERT INTO users (username) VALUES (?)",
                ("bob",),
            )
            conn.execute(
                "INSERT INTO accounts (account) VALUES (?)",
                ("hpc",),
            )
            conn.execute(
                "INSERT INTO accounts (account) VALUES (?)",
                ("dev",),
            )
            self.insert_associations(conn, ("alice", "hpc"))
            conn.commit()

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
                "INSERT INTO users (username) VALUES (?)",
                ("alice",),
            )
            conn.execute(
                "INSERT INTO accounts (account) VALUES (?)",
                ("hpc",),
            )
            conn.execute(
                "INSERT INTO accounts (account) VALUES (?)",
                ("dev",),
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
        resp = self._request_expecting_abort(
            "GET",
            "/stats",
            403,
            description="Not allowed to access endpoint without bearer token",
        )
        body = json.loads(resp.data)
        self.assertEqual(body["error"], "forbidden")

    def test_stats_rejects_invalid_token(self):
        init_database()
        resp = self._request_expecting_abort(
            "GET",
            "/stats",
            401,
            description="Unable to decode token: Not enough segments",
            headers={"Authorization": "Bearer invalid"},
        )
        body = json.loads(resp.data)
        self.assertEqual(body["error"], "unauthorized")

    def test_user_cannot_query_other_user_stats(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username) VALUES (?)",
                ("bob",),
            )
            conn.commit()

        self._request_expecting_abort(
            "GET",
            "/stats?username=bob",
            403,
            description="Not allowed to view stats for other users",
            headers=self._headers("carol"),
        )

    def test_operator_can_query_all_stats(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username) VALUES (?)",
                ("bob",),
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
                "INSERT INTO users (username) VALUES (?)",
                ("carol",),
            )
            conn.execute(
                "INSERT INTO users (username) VALUES (?)",
                ("bob",),
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
                "INSERT INTO accounts (account) VALUES (?)",
                ("hpc",),
            )
            conn.execute(
                "INSERT INTO accounts (account) VALUES (?)",
                ("dev",),
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

        self._request_expecting_abort(
            "GET",
            "/stats?account=dev",
            403,
            description="Not allowed to view stats for this account",
            headers=self._headers("carol"),
        )

    def test_manager_can_query_member_user_stats(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username) VALUES (?)",
                ("bob",),
            )
            conn.execute(
                "INSERT INTO accounts (account) VALUES (?)",
                ("hpc",),
            )
            grant_manager(conn, "carol")
            grant_manager_account(conn, "carol", "hpc")
            self.insert_associations(conn, ("bob", "hpc"), ("bob", "dev"))
            conn.commit()

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
                "INSERT INTO users (username) VALUES (?)",
                ("bob",),
            )
            grant_manager(conn, "carol")
            grant_manager_account(conn, "carol", "hpc")
            self.insert_associations(conn, ("bob", "dev"))
            conn.commit()

        self._request_expecting_abort(
            "GET",
            "/stats?username=bob",
            403,
            description="Not allowed to view stats for other users",
            headers=self._headers("carol"),
        )

    def test_manager_unfiltered_stats_returns_assigned_scope(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username) VALUES (?)",
                ("carol",),
            )
            conn.execute(
                "INSERT INTO users (username) VALUES (?)",
                ("bob",),
            )
            conn.execute(
                "INSERT INTO users (username) VALUES (?)",
                ("dave",),
            )
            conn.execute(
                "INSERT INTO accounts (account) VALUES (?)",
                ("hpc",),
            )
            conn.execute(
                "INSERT INTO accounts (account) VALUES (?)",
                ("dev",),
            )
            grant_manager(conn, "carol")
            grant_manager_account(conn, "carol", "hpc")
            self.insert_associations(conn, ("bob", "hpc"))
            conn.commit()

        client = app.test_client()
        resp = client.get("/stats", headers=self._headers("carol"))

        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        usernames = {entry["username"] for entry in body["users"]}
        accounts = {entry["account"] for entry in body["accounts"]}
        self.assertEqual(usernames, {"bob", "carol"})
        self.assertEqual(accounts, {"hpc"})

    def test_manager_unfiltered_includes_self_when_not_account_member(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username) VALUES (?)",
                ("carol",),
            )
            conn.execute(
                "INSERT INTO users (username) VALUES (?)",
                ("bob",),
            )
            conn.execute(
                "INSERT INTO accounts (account) VALUES (?)",
                ("hpc",),
            )
            grant_manager(conn, "carol")
            grant_manager_account(conn, "carol", "hpc")
            self.insert_associations(conn, ("bob", "hpc"))
            conn.commit()

        client = app.test_client()
        resp = client.get("/stats", headers=self._headers("carol"))

        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        usernames = [entry["username"] for entry in body["users"]]
        self.assertEqual(sorted(usernames), ["bob", "carol"])

    def test_manager_can_query_own_stats(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username) VALUES (?)",
                ("carol",),
            )
            conn.execute(
                "INSERT INTO accounts (account) VALUES (?)",
                ("hpc",),
            )
            grant_manager(conn, "carol")
            grant_manager_account(conn, "carol", "hpc")
            conn.commit()

        client = app.test_client()
        resp = client.get("/stats?username=carol", headers=self._headers("carol"))

        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        self.assertEqual(len(body["users"]), 1)
        self.assertEqual(body["users"][0]["username"], "carol")
        self.assertEqual(len(body["accounts"]), 1)
        self.assertEqual(body["accounts"][0]["account"], "hpc")

    def test_manager_with_no_assignments_sees_own_stats(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username) VALUES (?)",
                ("carol",),
            )
            conn.execute(
                "INSERT INTO users (username) VALUES (?)",
                ("bob",),
            )
            conn.execute(
                "INSERT INTO accounts (account) VALUES (?)",
                ("personal",),
            )
            grant_manager(conn, "carol")
            self.insert_associations(conn, ("carol", "personal"))
            conn.commit()

        client = app.test_client()
        for path in ("/stats", "/stats?username=carol"):
            with self.subTest(path=path):
                resp = client.get(path, headers=self._headers("carol"))
                self.assertEqual(resp.status_code, 200)
                body = json.loads(resp.data)
                self.assertEqual(len(body["users"]), 1)
                self.assertEqual(body["users"][0]["username"], "carol")
                self.assertEqual(len(body["accounts"]), 1)
                self.assertEqual(body["accounts"][0]["account"], "personal")

    def test_manager_can_query_personal_slurm_account(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO accounts (account) VALUES (?)",
                ("hpc",),
            )
            conn.execute(
                "INSERT INTO accounts (account) VALUES (?)",
                ("personal",),
            )
            grant_manager(conn, "carol")
            grant_manager_account(conn, "carol", "hpc")
            self.insert_associations(conn, ("carol", "personal"))
            conn.commit()

        client = app.test_client()
        resp = client.get("/stats?account=personal", headers=self._headers("carol"))

        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        self.assertEqual(len(body["accounts"]), 1)
        self.assertEqual(body["accounts"][0]["account"], "personal")

    def test_manager_unfiltered_includes_personal_slurm_accounts(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username) VALUES (?)",
                ("carol",),
            )
            conn.execute(
                "INSERT INTO users (username) VALUES (?)",
                ("bob",),
            )
            conn.execute(
                "INSERT INTO accounts (account) VALUES (?)",
                ("hpc",),
            )
            conn.execute(
                "INSERT INTO accounts (account) VALUES (?)",
                ("personal",),
            )
            grant_manager(conn, "carol")
            grant_manager_account(conn, "carol", "hpc")
            self.insert_associations(conn, ("bob", "hpc"), ("carol", "personal"))
            conn.commit()

        client = app.test_client()
        resp = client.get("/stats", headers=self._headers("carol"))

        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        accounts = {entry["account"] for entry in body["accounts"]}
        self.assertEqual(accounts, {"hpc", "personal"})

    def test_manager_user_in_multiple_assigned_accounts_appears_once(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username) VALUES (?)",
                ("carol",),
            )
            conn.execute(
                "INSERT INTO users (username) VALUES (?)",
                ("bob",),
            )
            conn.execute(
                "INSERT INTO accounts (account) VALUES (?)",
                ("hpc",),
            )
            conn.execute(
                "INSERT INTO accounts (account) VALUES (?)",
                ("dev",),
            )
            grant_manager(conn, "carol")
            grant_manager_account(conn, "carol", "hpc")
            grant_manager_account(conn, "carol", "dev")
            self.insert_associations(conn, ("bob", "hpc"), ("bob", "dev"))
            conn.commit()

        client = app.test_client()
        resp = client.get("/stats", headers=self._headers("carol"))

        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        bob_entries = [entry for entry in body["users"] if entry["username"] == "bob"]
        self.assertEqual(len(bob_entries), 1)
        usernames = [entry["username"] for entry in body["users"]]
        self.assertEqual(len(usernames), len(set(usernames)))


class TestStatsBreakdown(ServeRoutesTestCase):
    """GET /stats?breakdown=true: consumption per user/account pair."""

    def setUp(self):
        super().setUp()
        update_user_and_account_resources("dave", "hpc", 600, None, 60)
        update_user_and_account_resources("dave", "dev", 120, None, 0)
        update_user_and_account_resources("bob", "hpc", 300, None, 0)
        update_user_and_account_resources("bob", "dev", 30, None, 0)
        update_user_and_account_resources("(unattributed)", "hpc", 45, None, 0)
        with self.db_connection() as conn:
            self.insert_associations(
                conn,
                ("dave", "hpc"),
                ("dave", "dev"),
                ("bob", "hpc"),
                ("bob", "dev"),
                ("carol", "dev"),
            )
            conn.commit()

    def _breakdown(self, query: str, username: str) -> list[tuple[str, str]]:
        resp = app.test_client().get(
            f"/stats?{query}breakdown=true", headers=self._headers(username)
        )
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        return [(e["username"], e["account"]) for e in body["breakdown"]]

    def _grant_operator(self, username: str) -> None:
        with self.db_connection() as conn:
            grant_operator(conn, username)
            conn.commit()

    def _grant_manager(self, username: str, *accounts: str) -> None:
        with self.db_connection() as conn:
            grant_manager(conn, username)
            for account in accounts:
                grant_manager_account(conn, username, account)
            conn.commit()

    def _scoped_breakdown(self, username: str, *scopes: str) -> list[tuple[str, str]]:
        token = issue_test_token(self._site_ini, username, scopes)
        resp = app.test_client().get(
            "/stats?breakdown=true", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        return [(e["username"], e["account"]) for e in body["breakdown"]]

    def test_operator_token_restricted_to_own_stats(self):
        self._grant_operator("dave")
        self.assertEqual(
            self._scoped_breakdown("dave", "stats:read:own"),
            [("dave", "dev"), ("dave", "hpc")],
        )

    def test_manager_token_restricted_to_own_stats(self):
        self._grant_manager("bob", "hpc")
        self.assertEqual(
            self._scoped_breakdown("bob", "stats:read:own"),
            [("bob", "dev"), ("bob", "hpc")],
        )

    def test_manager_token_with_managed_stats(self):
        self._grant_manager("bob", "hpc")
        self.assertEqual(
            self._scoped_breakdown("bob", "stats:read:own", "stats:read:managed"),
            self._breakdown("", "bob"),
        )

    def test_token_without_stats_scope_refused(self):
        self._grant_operator("dave")
        token = issue_test_token(self._site_ini, "dave", ["quotas:read"])
        self._request_expecting_abort(
            "GET",
            "/stats",
            403,
            description="Token scope insufficient",
            headers={"Authorization": f"Bearer {token}"},
        )

    def test_me_accepts_token_without_stats_scope(self):
        token = issue_test_token(self._site_ini, "dave", ["quotas:read"])
        resp = app.test_client().get(
            "/me", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(resp.status_code, 200)

    def test_breakdown_absent_by_default(self):
        resp = app.test_client().get("/stats", headers=self._headers("dave"))
        self.assertNotIn("breakdown", json.loads(resp.data))

    def test_breakdown_entry_fields_in_seconds(self):
        resp = app.test_client().get(
            "/stats?breakdown=1", headers=self._headers("dave")
        )
        entries = json.loads(resp.data)["breakdown"]
        hpc = next(e for e in entries if e["account"] == "hpc")
        self.assertEqual(hpc["username"], "dave")
        self.assertEqual(hpc["consumed_cpu_seconds"], 600)
        self.assertEqual(hpc["consumed_gpu_seconds"], 60)
        self.assertIn("last_updated", hpc)

    def test_breakdown_false_value_omits_breakdown(self):
        resp = app.test_client().get(
            "/stats?breakdown=no", headers=self._headers("dave")
        )
        self.assertNotIn("breakdown", json.loads(resp.data))

    def test_operator_sees_all_pairs(self):
        self._grant_operator("carol")
        self.assertEqual(
            self._breakdown("", "carol"),
            [
                ("(unattributed)", "hpc"),
                ("bob", "dev"),
                ("bob", "hpc"),
                ("dave", "dev"),
                ("dave", "hpc"),
            ],
        )

    def test_operator_user_query_selects_user_pairs(self):
        self._grant_operator("carol")
        self.assertEqual(
            self._breakdown("username=bob&", "carol"),
            [("bob", "dev"), ("bob", "hpc")],
        )

    def test_operator_account_query_selects_account_pairs(self):
        self._grant_operator("carol")
        self.assertEqual(
            self._breakdown("account=hpc&", "carol"),
            [("(unattributed)", "hpc"), ("bob", "hpc"), ("dave", "hpc")],
        )

    def test_user_sees_own_pairs(self):
        self.assertEqual(
            self._breakdown("", "dave"), [("dave", "dev"), ("dave", "hpc")]
        )

    def test_user_account_query_restricted_to_own_pair(self):
        self.assertEqual(self._breakdown("account=hpc&", "dave"), [("dave", "hpc")])

    def test_manager_account_query_sees_all_account_users(self):
        self._grant_manager("carol", "hpc")
        self.assertEqual(
            self._breakdown("account=hpc&", "carol"),
            [("(unattributed)", "hpc"), ("bob", "hpc"), ("dave", "hpc")],
        )

    def test_manager_personal_account_restricted_to_own_pair(self):
        self._grant_manager("carol", "hpc")
        update_user_and_account_resources("carol", "dev", 10, None, 0)
        self.assertEqual(self._breakdown("account=dev&", "carol"), [("carol", "dev")])

    def test_manager_member_query_restricted_to_assigned_accounts(self):
        self._grant_manager("carol", "hpc")
        self.assertEqual(self._breakdown("username=bob&", "carol"), [("bob", "hpc")])

    def test_manager_unfiltered_sees_own_and_assigned_pairs(self):
        self._grant_manager("carol", "hpc")
        update_user_and_account_resources("carol", "dev", 10, None, 0)
        self.assertEqual(
            self._breakdown("", "carol"),
            [
                ("(unattributed)", "hpc"),
                ("bob", "hpc"),
                ("carol", "dev"),
                ("dave", "hpc"),
            ],
        )


class StatsVisibilityTestCase(SlurmQuotaTestCase):
    """Carol manages hpc, whose members are bob and carol. Carol also belongs
    to dev, with dave. Bob also belongs to gpu."""

    def setUp(self):
        super().setUp()
        init_database()
        with self.db_connection() as conn:
            grant_manager(conn, "carol")
            grant_manager_account(conn, "carol", "hpc")
            self.insert_associations(
                conn,
                ("bob", "hpc"),
                ("bob", "gpu"),
                ("carol", "hpc"),
                ("carol", "dev"),
                ("dave", "dev"),
            )
            conn.commit()


class TestStatsVisibility(StatsVisibilityTestCase):
    def test_all(self):
        visibility = StatsVisibility.for_level("carol", "stats:read:all")
        self.assertEqual(visibility, StatsVisibility())
        self.assertTrue(visibility.can_view_account("gpu"))
        self.assertTrue(
            visibility.can_view_pair({"username": "dave", "account": "dev"})
        )
        self.assertEqual(visibility.user_accounts("bob"), {"hpc", "gpu"})

    def test_managed(self):
        visibility = StatsVisibility.for_level("carol", "stats:read:managed")
        self.assertEqual(
            visibility,
            StatsVisibility(
                viewer="carol",
                users={"bob", "carol"},
                accounts={"hpc", "dev"},
                managed_accounts={"hpc"},
            ),
        )
        self.assertTrue(visibility.can_view_account("dev"))
        self.assertFalse(visibility.can_view_account("gpu"))

    def test_managed_pairs(self):
        visibility = StatsVisibility.for_level("carol", "stats:read:managed")
        self.assertTrue(
            visibility.can_view_pair({"username": "carol", "account": "dev"})
        )
        self.assertTrue(visibility.can_view_pair({"username": "bob", "account": "hpc"}))
        self.assertFalse(
            visibility.can_view_pair({"username": "dave", "account": "dev"})
        )
        self.assertFalse(
            visibility.can_view_pair({"username": "bob", "account": "gpu"})
        )

    def test_managed_user_accounts(self):
        visibility = StatsVisibility.for_level("carol", "stats:read:managed")
        self.assertEqual(visibility.user_accounts("carol"), {"hpc", "dev"})
        self.assertEqual(visibility.user_accounts("bob"), {"hpc"})
        self.assertIsNone(visibility.user_accounts("dave"))

    def test_own(self):
        visibility = StatsVisibility.for_level("carol", "stats:read:own")
        self.assertEqual(
            visibility,
            StatsVisibility(viewer="carol", users={"carol"}, accounts={"hpc", "dev"}),
        )
        self.assertFalse(
            visibility.can_view_pair({"username": "bob", "account": "hpc"})
        )
        self.assertEqual(visibility.user_accounts("carol"), {"hpc", "dev"})
        self.assertIsNone(visibility.user_accounts("bob"))
