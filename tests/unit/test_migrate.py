"""Unit tests for slurm_quota.migrate."""

from __future__ import annotations

from slurm_quota.database import init_database
from slurm_quota.migrate import migrate_database
from tests.test_support import SlurmQuotaTestCase


class TestMigrateDatabase(SlurmQuotaTestCase):
    def test_migrate_database_enables_wal(self):
        init_database()
        with self.db_connection() as conn:
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.commit()
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual(mode.lower(), "delete")

        migrate_database()

        with self.db_connection() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual(mode.lower(), "wal")
