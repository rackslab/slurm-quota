"""Shared base for serve route unit tests."""

from __future__ import annotations

import tempfile
from pathlib import Path
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
