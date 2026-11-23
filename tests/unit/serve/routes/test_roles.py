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
                "INSERT INTO users (username) VALUES (?)",
                ("bob",),
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
        self._request_expecting_abort(
            "GET",
            "/roles",
            403,
            description="Insufficient permissions",
            headers=self._headers("carol"),
        )

    def test_non_admin_cannot_grant_operator(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username) VALUES (?)",
                ("bob",),
            )
            conn.commit()

        self._request_expecting_abort(
            "PUT",
            "/roles/operators/bob",
            403,
            description="Insufficient permissions",
            headers=self._headers("carol"),
        )

    def test_non_admin_cannot_grant_manager(self):
        self._request_expecting_abort(
            "PUT",
            "/roles/managers/bob",
            403,
            description="Insufficient permissions",
            headers=self._headers("carol"),
        )

    def test_admin_grants_and_revokes_operator(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username) VALUES (?)",
                ("bob",),
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
                "INSERT INTO users (username) VALUES (?)",
                ("bob",),
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
        self._request_expecting_abort(
            "PUT",
            "/roles/managers/bob/accounts/hpc",
            404,
            description="User is not a manager",
            headers=self._headers("alice"),
        )


class TestRolesLogs(ServeRoutesTestCase):
    def test_role_grant_and_revoke_are_logged(self):
        init_database()
        client = app.test_client()
        with self.assertLogs("slurm_quota", level="INFO") as log_cm:
            client.put("/roles/operators/bob", headers=self._headers("alice"))
            client.delete("/roles/operators/bob", headers=self._headers("alice"))
            client.put("/roles/managers/bob", headers=self._headers("alice"))
            client.put(
                "/roles/managers/bob/accounts/hpc", headers=self._headers("alice")
            )
            client.delete(
                "/roles/managers/bob/accounts/hpc", headers=self._headers("alice")
            )
            client.delete("/roles/managers/bob", headers=self._headers("alice"))
        self.assertEqual(
            [line for line in log_cm.output if "role" in line or "account" in line],
            [
                "INFO:slurm_quota:role granted: user=bob role=operator by=alice",
                "INFO:slurm_quota:role revoked: user=bob role=operator by=alice",
                "INFO:slurm_quota:role granted: user=bob role=manager by=alice",
                "INFO:slurm_quota:manager account granted: user=bob account=hpc "
                "by=alice",
                "INFO:slurm_quota:manager account revoked: user=bob account=hpc "
                "by=alice",
                "INFO:slurm_quota:role revoked: user=bob role=manager by=alice",
            ],
        )

    def test_role_grant_to_configured_admin_is_logged_as_ignored(self):
        init_database()
        client = app.test_client()
        with self.assertLogs("slurm_quota", level="INFO") as log_cm:
            resp = client.put("/roles/operators/alice", headers=self._headers("alice"))
        self.assertEqual(resp.status_code, 204)
        self.assertIn(
            "INFO:slurm_quota:role grant ignored: user=alice role=operator by=alice "
            "reason=admin in configuration",
            log_cm.output,
        )

    def test_access_denied_is_logged_with_role(self):
        init_database()
        client = app.test_client()
        with self.assertLogs("slurm_quota", level="INFO") as log_cm:
            resp = client.get("/roles", headers=self._headers("carol"))
        self.assertEqual(resp.status_code, 403)
        self.assertIn(
            "INFO:slurm_quota:access denied: user=carol role=user kind=user "
            "path=/roles required=roles:read reason=insufficient permissions",
            log_cm.output,
        )
