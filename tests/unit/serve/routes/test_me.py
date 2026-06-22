"""Unit tests for GET /me."""

from __future__ import annotations

import json

from tests.unit.serve.routes.base import ServeRoutesTestCase, app


class TestMeRoute(ServeRoutesTestCase):
    def test_me_returns_role(self):
        client = app.test_client()
        resp = client.get("/me", headers=self._headers("alice"))
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        self.assertEqual(body, {"username": "alice", "role": "admin"})
