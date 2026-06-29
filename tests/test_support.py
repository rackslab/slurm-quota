from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch

from slurm_quota.database import configure_connection


class SlurmQuotaTestCase(unittest.TestCase):
    """Use ``with self.db_connection() as conn:`` for any direct SQLite access in tests."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmp.name) / "slurm-quota.db")
        self._patch_db = patch("slurm_quota.DB_PATH", self.db_path)
        self._patch_db.start()

    def tearDown(self):
        try:
            self._patch_db.stop()
        finally:
            self._tmp.cleanup()

    @contextmanager
    def db_connection(self):
        # sqlite3.Connection.__exit__ commits/rolls back but does not close; use closing().
        with closing(sqlite3.connect(self.db_path)) as conn:
            configure_connection(conn)
            yield conn

    def env(self, updates: dict[str, str]):
        p = patch.dict(os.environ, updates, clear=False)
        p.start()
        self.addCleanup(p.stop)
        return p
