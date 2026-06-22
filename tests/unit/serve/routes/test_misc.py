"""Unit tests for miscellaneous serve routes."""

from __future__ import annotations

import json

from tests.unit.serve.routes.base import ServeRoutesTestCase, app


class TestMiscRoutes(ServeRoutesTestCase):
    def test_unknown_path_returns_not_found(self):
        client = app.test_client()
        resp = client.get("/does-not-exist")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(json.loads(resp.data), {"error": "not_found"})
