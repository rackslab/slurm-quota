"""Shared base for serve route unit tests."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

from slurm_quota.database import init_database
from tests.test_support import SlurmQuotaTestCase, serve_conf_defs
from tests.unit.serve.support import (
    issue_test_token,
    registered_app,
    write_jwt_site_ini,
)

app = registered_app()


class ServeRoutesTestCase(SlurmQuotaTestCase):
    def setUp(self):
        super().setUp()
        self._patch_slurm = patch("slurm_quota.serve.app.auth.require_slurm_user")
        self._patch_slurm.start()
        self.addCleanup(self._patch_slurm.stop)
        init_database()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._site_ini = write_jwt_site_ini(Path(self._tmpdir.name))
        app.setup(serve_conf_defs(), self._site_ini)

    def _headers(self, username: str = "alice") -> dict[str, str]:
        return {"Authorization": f"Bearer {issue_test_token(self._site_ini, username)}"}

    def _assert_abort_warning(
        self,
        log_cm: Any,
        method: str,
        path: str,
        status: int,
        description: str,
    ) -> None:
        expected = f"WARNING:slurm_quota:{method} {path}: HTTP {status} {description}"
        self.assertIn(expected, log_cm.output)

    def _request_expecting_abort(
        self,
        method: str,
        path: str,
        status: int,
        *,
        description: str,
        **kwargs: Any,
    ) -> Any:
        client = app.test_client()
        request = getattr(client, method.lower())
        with self.assertLogs("slurm_quota", level="WARNING") as log_cm:
            resp = request(path, **kwargs)
        self.assertEqual(resp.status_code, status)
        self._assert_abort_warning(
            log_cm,
            method.upper(),
            path.split("?", 1)[0],
            status,
            description,
        )
        return resp
