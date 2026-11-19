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

    def test_migrate_database_creates_associations_table(self):
        with self.db_connection() as conn:
            conn.executescript(V3_SCHEMA)
            conn.commit()

        migrate_database()

        with self.db_connection() as conn:
            self.insert_associations(conn, ("alice", "hpc"))
            rows = conn.execute(
                "SELECT username, account FROM slurm_associations"
            ).fetchall()
        self.assertEqual(rows, [("alice", "hpc")])


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
            # Preallocations are kept, converted to seconds, and foreign keys
            # still enforced
            self.assertEqual(
                conn.execute(
                    "SELECT job_uuid, preallocated_cpu_seconds FROM jobs_preallocations"
                ).fetchall(),
                [("j1", 300)],
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
            {k: v["total_consumed_cpu_seconds"] for k, v in users.items()},
            {UNATTRIBUTED: 13 * 60, "u1": 7 * 60, "u2": 5 * 60, "u3": 60},
        )
        self.assertEqual(
            {k: v["total_consumed_gpu_seconds"] for k, v in users.items()},
            {UNATTRIBUTED: 4 * 60, "u1": 3 * 60, "u2": 0, "u3": 60},
        )
        self.assertEqual(
            {k: v["total_consumed_cpu_seconds"] for k, v in accounts.items()},
            {UNATTRIBUTED: 13 * 60, "a1": 3 * 60, "a2": 10 * 60},
        )
        self.assertEqual(
            {k: v["total_consumed_gpu_seconds"] for k, v in accounts.items()},
            {UNATTRIBUTED: 4 * 60, "a1": 4 * 60, "a2": 0},
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
                INSERT INTO jobs_preallocations (
                    job_uuid, username, account, preallocated_cpu_minutes
                ) VALUES ('j1', 'u1', 'a1', 3);
                """
            )
        migrate_database()
        with self.db_connection() as conn:
            self.assertEqual(self.pair_consumption(conn, "u1", UNATTRIBUTED), (240, 0))
            self.assertEqual(self.pair_consumption(conn, UNATTRIBUTED, "a1"), (240, 0))
            columns = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
            preallocations = conn.execute(
                "SELECT job_uuid, preallocated_cpu_seconds, preallocated_gpu_seconds, "
                "array_size FROM jobs_preallocations"
            ).fetchall()
        self.assertEqual(
            columns,
            ["username", "quota_cpu_minutes", "quota_gpu_minutes", "last_updated"],
        )
        # Columns added by intermediate steps are converted along
        self.assertEqual(preallocations, [("j1", 180, 0, 1)])

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
                    sqlite3.IntegrityError,
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


PREALLOCATION_COLUMNS = [
    "job_uuid",
    "username",
    "account",
    "preallocated_cpu_seconds",
    "preallocated_gpu_seconds",
    "array_size",
    "created_at",
]


class TestMigratePreallocationsToSeconds(SlurmQuotaTestCase):
    def _create_v3_database(self):
        with self.db_connection() as conn:
            conn.executescript(V3_SCHEMA)
            conn.execute("INSERT INTO users (username) VALUES ('u1')")
            conn.execute("INSERT INTO accounts (account) VALUES ('a1')")
            conn.executemany(
                """
                INSERT INTO jobs_preallocations (
                    job_uuid, username, account, preallocated_cpu_minutes,
                    preallocated_gpu_minutes, array_size, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("j1", "u1", "a1", 5, 2, 3, "2026-01-01 10:00:00"),
                    ("j2", "u1", "a1", 7, None, None, "2026-01-02 10:00:00"),
                ],
            )
            conn.commit()

    def _preallocations(self, conn):
        return conn.execute(
            f"SELECT {', '.join(PREALLOCATION_COLUMNS)} "
            "FROM jobs_preallocations ORDER BY job_uuid"
        ).fetchall()

    def test_converts_preallocations_to_seconds(self):
        self._create_v3_database()
        with self.assertLogs("slurm_quota", level="INFO") as log_cm:
            migrate_database()
        self.assertIn(
            "INFO:slurm_quota:Migration completed: 2 jobs preallocations converted "
            "to seconds",
            log_cm.output,
        )
        with self.db_connection() as conn:
            columns = [
                r[1] for r in conn.execute("PRAGMA table_info(jobs_preallocations)")
            ]
            self.assertEqual(columns, PREALLOCATION_COLUMNS)
            self.assertEqual(
                self._preallocations(conn),
                [
                    ("j1", "u1", "a1", 300, 120, 3, "2026-01-01 10:00:00"),
                    ("j2", "u1", "a1", 420, 0, 1, "2026-01-02 10:00:00"),
                ],
            )
            # Foreign keys to users and accounts are kept
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO jobs_preallocations (job_uuid, username, account, "
                    "preallocated_cpu_seconds) VALUES ('j3', 'nobody', 'a1', 60)"
                )

    def test_aggregates_match_legacy_preallocations(self):
        self._create_v3_database()
        migrate_database()
        with self.db_connection() as conn:
            (user,) = query_users_aggregate(conn, {"u1"})
        self.assertEqual(user["total_preallocated_cpu_seconds"], (5 * 3 + 7) * 60)
        self.assertEqual(user["total_preallocated_gpu_seconds"], 2 * 3 * 60)
        self.assertEqual(user["job_count"], 4)

    def test_migration_is_idempotent(self):
        self._create_v3_database()
        migrate_database()
        with self.assertLogs("slurm_quota", level="INFO") as log_cm:
            migrate_database()
        self.assertIn(
            "INFO:slurm_quota:Migration not needed: preallocations already stored "
            "in seconds",
            log_cm.output,
        )
        with self.db_connection() as conn:
            columns = [
                r[1] for r in conn.execute("PRAGMA table_info(jobs_preallocations)")
            ]
            # Legacy minutes columns are not added back
            self.assertEqual(columns, PREALLOCATION_COLUMNS)
            self.assertEqual(len(self._preallocations(conn)), 2)

    def test_new_database_needs_no_migration(self):
        init_database()
        with self.assertLogs("slurm_quota", level="INFO") as log_cm:
            migrate_database()
        self.assertIn(
            "INFO:slurm_quota:Migration not needed: preallocations already stored "
            "in seconds",
            log_cm.output,
        )

    def test_failure_rolls_back_all_v4_steps(self):
        self._create_v3_database()
        with self.db_connection() as conn:
            conn.execute(
                "UPDATE users SET total_consumed_cpu_minutes = 10 WHERE username = 'u1'"
            )
            conn.commit()
        with (
            patch(
                "slurm_quota.migrate.create_preallocations_table",
                side_effect=sqlite3.OperationalError("boom"),
            ),
            self.assertRaisesRegex(sqlite3.OperationalError, "boom"),
        ):
            migrate_database()
        with self.db_connection() as conn:
            # Consumption migration, run before, is rolled back too
            user_columns = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
            self.assertIn("total_consumed_cpu_minutes", user_columns)
            self.assertIsNone(
                conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE name IN ('user_account_consumption', "
                    "'jobs_preallocations_legacy')"
                ).fetchone()
            )
            columns = [
                r[1] for r in conn.execute("PRAGMA table_info(jobs_preallocations)")
            ]
            self.assertIn("preallocated_cpu_minutes", columns)
            self.assertEqual(
                conn.execute(
                    "SELECT job_uuid, preallocated_cpu_minutes "
                    "FROM jobs_preallocations ORDER BY job_uuid"
                ).fetchall(),
                [("j1", 5), ("j2", 7)],
            )
