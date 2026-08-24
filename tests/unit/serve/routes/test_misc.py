"""Unit tests for miscellaneous serve routes."""

from __future__ import annotations

import json

from werkzeug.exceptions import NotFound

from tests.unit.serve.routes.base import ServeRoutesTestCase


class TestMiscRoutes(ServeRoutesTestCase):
    def test_unknown_path_returns_not_found(self):
        resp = self._request_expecting_abort(
            "GET",
            "/does-not-exist",
            404,
            description=NotFound.description,
        )
        self.assertEqual(json.loads(resp.data), {"error": "not_found"})
