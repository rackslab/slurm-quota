"""Unit tests for GET /me."""

from __future__ import annotations

import json

from slurm_quota.database import grant_manager, grant_manager_account, init_database
from tests.unit.serve.routes.base import ServeRoutesTestCase, app


class TestMeRoute(ServeRoutesTestCase):
    def test_me_returns_role(self):
        client = app.test_client()
        resp = client.get("/me", headers=self._headers("alice"))
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        self.assertEqual(body, {"username": "alice", "role": "admin"})

    def test_me_returns_manager_accounts(self):
        init_database()
        with self.db_connection() as conn:
            grant_manager(conn, "carol")
            grant_manager_account(conn, "carol", "dev")
            grant_manager_account(conn, "carol", "hpc")
            conn.commit()

        client = app.test_client()
        resp = client.get("/me", headers=self._headers("carol"))
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        self.assertEqual(body["username"], "carol")
        self.assertEqual(body["role"], "manager")
        self.assertEqual(body["accounts"], ["dev", "hpc"])
