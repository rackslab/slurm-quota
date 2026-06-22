"""Unit tests for GET /health."""

from __future__ import annotations

import json

from tests.unit.serve.routes.base import ServeRoutesTestCase, app


class TestHealthRoute(ServeRoutesTestCase):
    def test_health_returns_ok(self):
        client = app.test_client()
        resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.data), {"status": "ok"})
