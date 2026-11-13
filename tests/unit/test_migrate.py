"""Unit tests for slurm_quota.migrate."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

from slurm_quota.database import (
    init_database,
    query_accounts_aggregate,
    query_users_aggregate,
)
from slurm_quota.migrate import (
    CONSUMPTION_MIGRATED_AT,
    UNATTRIBUTED,
    ConsumptionMigrationError,
    migrate_database,
)
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


V3_SCHEMA = """
CREATE TABLE users (
    username TEXT PRIMARY KEY,
    total_consumed_cpu_minutes INTEGER DEFAULT 0,
    quota_cpu_minutes INTEGER DEFAULT -1,
    total_consumed_gpu_minutes INTEGER DEFAULT 0,
    quota_gpu_minutes INTEGER DEFAULT -1,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE accounts (
    account TEXT PRIMARY KEY,
    total_consumed_cpu_minutes INTEGER DEFAULT 0,
    quota_cpu_minutes INTEGER DEFAULT -1,
    total_consumed_gpu_minutes INTEGER DEFAULT 0,
    quota_gpu_minutes INTEGER DEFAULT -1,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE jobs_preallocations (
    job_uuid TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    account TEXT NOT NULL,
    preallocated_cpu_minutes INTEGER NOT NULL,
    preallocated_gpu_minutes INTEGER DEFAULT 0,
    array_size INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (username) REFERENCES users (username),
    FOREIGN KEY (account) REFERENCES accounts (account)
);
"""


class TestMigrateConsumptionToPairs(SlurmQuotaTestCase):
    def _create_v3_database(self, users, accounts):
        with self.db_connection() as conn:
            conn.executescript(V3_SCHEMA)
            conn.executemany(
                """
                INSERT INTO users (
                    username, total_consumed_cpu_minutes, quota_cpu_minutes,
                    total_consumed_gpu_minutes, quota_gpu_minutes
                ) VALUES (?, ?, ?, ?, ?)
                """,
                users,
            )
            conn.executemany(
                """
                INSERT INTO accounts (
                    account, total_consumed_cpu_minutes, quota_cpu_minutes,
                    total_consumed_gpu_minutes, quota_gpu_minutes
                ) VALUES (?, ?, ?, ?, ?)
                """,
                accounts,
            )
            conn.execute(
                """
                INSERT INTO jobs_preallocations (
                    job_uuid, username, account, preallocated_cpu_minutes
                ) VALUES ('j1', 'u1', 'a1', 5)
                """
            )
            conn.commit()

    def _pairs(self, conn):
        return conn.execute(
            "SELECT username, account, consumed_cpu_seconds, consumed_gpu_seconds "
            "FROM user_account_consumption ORDER BY username, account"
        ).fetchall()

    def test_migrates_consumption_to_unattributed_pairs(self):
        self._create_v3_database(
            users=[
                ("u1", 300, 1000, 20, 50),
                ("u2", 100, -1, 0, -1),
                ("u3", 0, 5, 0, 5),
            ],
            accounts=[("a1", 200, 2000, 20, -1), ("a2", 205, -1, 0, 7)],
        )
        with patch("slurm_quota.migrate.time.time", return_value=1_700_000_000.5):
            migrate_database()

        with self.db_connection() as conn:
            user_columns = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
            account_columns = [
                r[1] for r in conn.execute("PRAGMA table_info(accounts)")
            ]
            self.assertEqual(
                user_columns,
                ["username", "quota_cpu_minutes", "quota_gpu_minutes", "last_updated"],
            )
            self.assertEqual(
                account_columns,
                ["account", "quota_cpu_minutes", "quota_gpu_minutes", "last_updated"],
            )
            # Quotas are preserved, unattributed entities have unlimited quotas
            self.assertEqual(
                conn.execute(
                    "SELECT username, quota_cpu_minutes, quota_gpu_minutes "
                    "FROM users ORDER BY username"
                ).fetchall(),
                [
                    (UNATTRIBUTED, -1, -1),
                    ("u1", 1000, 50),
                    ("u2", -1, -1),
                    ("u3", 5, 5),
                ],
            )
            self.assertEqual(
                conn.execute(
                    "SELECT account, quota_cpu_minutes, quota_gpu_minutes "
                    "FROM accounts ORDER BY account"
                ).fetchall(),
                [(UNATTRIBUTED, -1, -1), ("a1", 2000, -1), ("a2", -1, 7)],
            )
            # Legacy consumption is copied in seconds on unattributed pairs
            self.assertEqual(
                self._pairs(conn),
                [
                    (UNATTRIBUTED, "a1", 200 * 60, 20 * 60),
                    (UNATTRIBUTED, "a2", 205 * 60, 0),
                    ("u1", UNATTRIBUTED, 300 * 60, 20 * 60),
                    ("u2", UNATTRIBUTED, 100 * 60, 0),
                ],
            )
            self.assertEqual(
                conn.execute(
                    "SELECT value FROM settings WHERE key = ?",
                    (CONSUMPTION_MIGRATED_AT,),
                ).fetchone(),
                ("1700000000",),
            )
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            # Preallocations are untouched and foreign keys still enforced
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM jobs_preallocations").fetchone()[0],
                1,
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO user_account_consumption (username, account) "
                    "VALUES ('nobody', 'a1')"
                )

    def test_aggregates_match_legacy_totals(self):
        self._create_v3_database(
            users=[("u1", 7, -1, 3, -1), ("u2", 5, -1, 0, -1), ("u3", 1, -1, 1, -1)],
            accounts=[("a1", 3, -1, 4, -1), ("a2", 10, -1, 0, -1)],
        )
        migrate_database()
        with self.db_connection() as conn:
            users = {u["username"]: u for u in query_users_aggregate(conn)}
            accounts = {a["account"]: a for a in query_accounts_aggregate(conn)}
        # Unattributed user and account are visible as regular entities
        self.assertEqual(
            {k: v["total_consumed_cpu_minutes"] for k, v in users.items()},
            {UNATTRIBUTED: 13, "u1": 7, "u2": 5, "u3": 1},
        )
        self.assertEqual(
            {k: v["total_consumed_gpu_minutes"] for k, v in users.items()},
            {UNATTRIBUTED: 4, "u1": 3, "u2": 0, "u3": 1},
        )
        self.assertEqual(
            {k: v["total_consumed_cpu_minutes"] for k, v in accounts.items()},
            {UNATTRIBUTED: 13, "a1": 3, "a2": 10},
        )
        self.assertEqual(
            {k: v["total_consumed_gpu_minutes"] for k, v in accounts.items()},
            {UNATTRIBUTED: 4, "a1": 4, "a2": 0},
        )

    def test_without_legacy_consumption_creates_no_unattributed_entities(self):
        self._create_v3_database(
            users=[("u1", 0, 10, 0, -1)], accounts=[("a1", 0, -1, 0, -1)]
        )
        migrate_database()
        with self.db_connection() as conn:
            self.assertEqual(self._pairs(conn), [])
            self.assertEqual(
                [r[0] for r in conn.execute("SELECT username FROM users")], ["u1"]
            )
            self.assertEqual(
                [r[0] for r in conn.execute("SELECT account FROM accounts")], ["a1"]
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT value FROM settings WHERE key = ?",
                    (CONSUMPTION_MIGRATED_AT,),
                ).fetchone()
            )

    def test_migration_is_idempotent(self):
        self._create_v3_database(
            users=[("u1", 10, -1, 0, -1)], accounts=[("a1", 10, -1, 0, -1)]
        )
        migrate_database()
        with self.assertLogs("slurm_quota", level="INFO") as log_cm:
            migrate_database()
        self.assertIn(
            "INFO:slurm_quota:Migration not needed: consumption already stored per pair",
            log_cm.output,
        )
        with self.db_connection() as conn:
            self.assertEqual(
                self._pairs(conn),
                [(UNATTRIBUTED, "a1", 600, 0), ("u1", UNATTRIBUTED, 600, 0)],
            )
            # Legacy consumption columns are not added back
            for table in ("users", "accounts"):
                columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
                self.assertNotIn("total_consumed_cpu_minutes", columns)
                self.assertNotIn("total_consumed_gpu_minutes", columns)

    def test_migrates_database_without_gpu_columns(self):
        with self.db_connection() as conn:
            conn.executescript(
                """
                CREATE TABLE users (
                    username TEXT PRIMARY KEY,
                    total_consumed_cpu_minutes INTEGER DEFAULT 0,
                    quota_cpu_minutes INTEGER DEFAULT -1,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE accounts (
                    account TEXT PRIMARY KEY,
                    total_consumed_cpu_minutes INTEGER DEFAULT 0,
                    quota_cpu_minutes INTEGER DEFAULT -1,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE jobs_preallocations (
                    job_uuid TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    account TEXT NOT NULL,
                    preallocated_cpu_minutes INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO users (username, total_consumed_cpu_minutes)
                VALUES ('u1', 4);
                INSERT INTO accounts (account, total_consumed_cpu_minutes)
                VALUES ('a1', 4);
                """
            )
        migrate_database()
        with self.db_connection() as conn:
            self.assertEqual(self.pair_consumption(conn, "u1", UNATTRIBUTED), (240, 0))
            self.assertEqual(self.pair_consumption(conn, UNATTRIBUTED, "a1"), (240, 0))
            columns = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
        self.assertEqual(
            columns,
            ["username", "quota_cpu_minutes", "quota_gpu_minutes", "last_updated"],
        )

    def test_new_database_needs_no_migration(self):
        init_database()
        with self.assertLogs("slurm_quota", level="INFO") as log_cm:
            migrate_database()
        self.assertIn(
            "INFO:slurm_quota:Migration not needed: consumption already stored per pair",
            log_cm.output,
        )

    def test_existing_unattributed_name_aborts_and_rolls_back(self):
        for table, rows in (
            ("users", {"users": [(UNATTRIBUTED, 1, -1, 0, -1)], "accounts": []}),
            ("accounts", {"users": [], "accounts": [(UNATTRIBUTED, 1, -1, 0, -1)]}),
        ):
            with self.subTest(table=table):
                self.tearDown()
                self.setUp()
                self._create_v3_database(
                    users=[("u1", 10, -1, 0, -1), *rows["users"]],
                    accounts=[("a1", 10, -1, 0, -1), *rows["accounts"]],
                )
                with self.assertRaisesRegex(
                    ConsumptionMigrationError,
                    r"\(unattributed\) already exists, this name is reserved",
                ):
                    migrate_database()
                with self.db_connection() as conn:
                    user_columns = [
                        r[1] for r in conn.execute("PRAGMA table_info(users)")
                    ]
                    self.assertIn("total_consumed_cpu_minutes", user_columns)
                    self.assertIsNone(
                        conn.execute(
                            "SELECT name FROM sqlite_master "
                            "WHERE name = 'user_account_consumption'"
                        ).fetchone()
                    )
