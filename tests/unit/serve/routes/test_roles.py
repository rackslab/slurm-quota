"""Unit tests for /roles, /roles/operators, and /roles/managers endpoints."""

from __future__ import annotations

import json

from slurm_quota.database import (
    grant_manager,
    grant_manager_account,
    grant_operator,
    init_database,
)

from tests.unit.serve.routes.base import ServeRoutesTestCase, app


class TestRolesRoute(ServeRoutesTestCase):
    def test_admin_lists_users_with_roles(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("bob", 10),
            )
            grant_operator(conn, "carol")
            conn.commit()

        client = app.test_client()
        resp = client.get("/roles", headers=self._headers("alice"))
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        roles = {entry["username"]: entry["role"] for entry in body["users"]}
        self.assertEqual(roles["alice"], "admin")
        self.assertEqual(roles["bob"], "user")
        self.assertEqual(roles["carol"], "operator")

    def test_admin_lists_manager_accounts(self):
        init_database()
        with self.db_connection() as conn:
            grant_manager(conn, "bob")
            grant_manager_account(conn, "bob", "hpc")
            conn.commit()

        client = app.test_client()
        resp = client.get("/roles", headers=self._headers("alice"))
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        bob = next(entry for entry in body["users"] if entry["username"] == "bob")
        self.assertEqual(bob["role"], "manager")
        self.assertEqual(bob["accounts"], ["hpc"])

    def test_non_admin_cannot_list_roles(self):
        client = app.test_client()
        resp = client.get("/roles", headers=self._headers("carol"))
        self.assertEqual(resp.status_code, 403)

    def test_non_admin_cannot_grant_operator(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("bob", 5),
            )
            conn.commit()

        client = app.test_client()
        grant = client.put("/roles/operators/bob", headers=self._headers("carol"))
        self.assertEqual(grant.status_code, 403)

    def test_non_admin_cannot_grant_manager(self):
        init_database()
        client = app.test_client()
        grant = client.put("/roles/managers/bob", headers=self._headers("carol"))
        self.assertEqual(grant.status_code, 403)

    def test_admin_grants_and_revokes_operator(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("bob", 5),
            )
            conn.commit()

        client = app.test_client()
        grant = client.put("/roles/operators/bob", headers=self._headers("alice"))
        self.assertEqual(grant.status_code, 204)

        list_resp = client.get("/roles", headers=self._headers("alice"))
        roles = {
            entry["username"]: entry["role"]
            for entry in json.loads(list_resp.data)["users"]
        }
        self.assertEqual(roles["bob"], "operator")

        revoke = client.delete("/roles/operators/bob", headers=self._headers("alice"))
        self.assertEqual(revoke.status_code, 204)

        list_resp = client.get("/roles", headers=self._headers("alice"))
        roles = {
            entry["username"]: entry["role"]
            for entry in json.loads(list_resp.data)["users"]
        }
        self.assertEqual(roles.get("bob"), "user")

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

    def test_admin_manages_manager_accounts(self):
        init_database()
        with self.db_connection() as conn:
            grant_manager(conn, "bob")
            conn.commit()

        client = app.test_client()
        add = client.put(
            "/roles/managers/bob/accounts/hpc",
            headers=self._headers("alice"),
        )
        self.assertEqual(add.status_code, 204)

        list_accounts = client.get(
            "/roles/managers/bob/accounts",
            headers=self._headers("alice"),
        )
        self.assertEqual(list_accounts.status_code, 200)
        self.assertEqual(json.loads(list_accounts.data), {"accounts": ["hpc"]})

        remove = client.delete(
            "/roles/managers/bob/accounts/hpc",
            headers=self._headers("alice"),
        )
        self.assertEqual(remove.status_code, 204)

        list_accounts = client.get(
            "/roles/managers/bob/accounts",
            headers=self._headers("alice"),
        )
        self.assertEqual(json.loads(list_accounts.data), {"accounts": []})

    def test_manager_account_assignment_requires_manager_role(self):
        init_database()
        client = app.test_client()
        add = client.put(
            "/roles/managers/bob/accounts/hpc",
            headers=self._headers("alice"),
        )
        self.assertEqual(add.status_code, 404)
