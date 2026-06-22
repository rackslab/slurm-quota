"""Unit tests for /roles and /roles/managers endpoints."""

from __future__ import annotations

import json

from slurm_quota.database import grant_api_manager, init_database

from tests.unit.serve.routes.base import ServeRoutesTestCase, app


class TestRolesRoute(ServeRoutesTestCase):
    def test_admin_lists_users_with_roles(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("bob", 10),
            )
            grant_api_manager(conn, "carol")
            conn.commit()

        client = app.test_client()
        resp = client.get("/roles", headers=self._headers("alice"))
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        roles = {entry["username"]: entry["role"] for entry in body["users"]}
        self.assertEqual(roles["alice"], "admin")
        self.assertEqual(roles["bob"], "user")
        self.assertEqual(roles["carol"], "manager")

    def test_non_admin_cannot_list_roles(self):
        client = app.test_client()
        resp = client.get("/roles", headers=self._headers("carol"))
        self.assertEqual(resp.status_code, 403)

    def test_non_admin_cannot_grant_manager(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("bob", 5),
            )
            conn.commit()

        client = app.test_client()
        grant = client.put("/roles/managers/bob", headers=self._headers("carol"))
        self.assertEqual(grant.status_code, 403)

        list_resp = client.get("/roles", headers=self._headers("alice"))
        roles = {
            entry["username"]: entry["role"]
            for entry in json.loads(list_resp.data)["users"]
        }
        self.assertEqual(roles.get("bob"), "user")

    def test_non_admin_cannot_revoke_manager(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("bob", 5),
            )
            grant_api_manager(conn, "bob")
            conn.commit()

        client = app.test_client()
        revoke = client.delete("/roles/managers/bob", headers=self._headers("carol"))
        self.assertEqual(revoke.status_code, 403)

        list_resp = client.get("/roles", headers=self._headers("alice"))
        roles = {
            entry["username"]: entry["role"]
            for entry in json.loads(list_resp.data)["users"]
        }
        self.assertEqual(roles["bob"], "manager")

    def test_admin_grants_and_revokes_manager(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("bob", 5),
            )
            conn.commit()

        client = app.test_client()
        grant = client.put("/roles/managers/bob", headers=self._headers("alice"))
        self.assertEqual(grant.status_code, 204)

        list_resp = client.get("/roles", headers=self._headers("alice"))
        roles = {
            entry["username"]: entry["role"]
            for entry in json.loads(list_resp.data)["users"]
        }
        self.assertEqual(roles["bob"], "manager")

        revoke = client.delete("/roles/managers/bob", headers=self._headers("alice"))
        self.assertEqual(revoke.status_code, 204)

        list_resp = client.get("/roles", headers=self._headers("alice"))
        roles = {
            entry["username"]: entry["role"]
            for entry in json.loads(list_resp.data)["users"]
        }
        self.assertEqual(roles.get("bob"), "user")
