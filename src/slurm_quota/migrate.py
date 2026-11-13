# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Database migration CLI for slurm-quota."""

import argparse
import os
import sqlite3
import sys
import time

import slurm_quota
from slurm_quota.auth import get_current_user
from slurm_quota.cli import add_common_arguments
from slurm_quota.database import (
    connect_database,
    create_consumption_table,
    enable_wal_mode,
    set_database_permissions,
)
from slurm_quota.log import logger, setup_logging

MIGRATE_DEFAULT_QUOTA_SETTINGS = {
    "default_user_quota_cpu_minutes": "-1",
    "default_user_quota_gpu_minutes": "-1",
    "default_account_quota_cpu_minutes": "-1",
    "default_account_quota_gpu_minutes": "-1",
}


# Temporary user and account name holding legacy consumption that is not yet
# attributed to actual user/account pairs. Legacy consumption of a user is
# stored on the pair (user, UNATTRIBUTED) and legacy consumption of an account
# on the pair (UNATTRIBUTED, account), so that user and account totals are kept
# exactly. These rows, and this user and account, are removed by
# slurm-quota-reallocate. The rest of slurm-quota handles them as any other
# user, account and pair.
UNATTRIBUTED = "(unattributed)"

# Setting storing the time of migration to user/account pairs (epoch seconds),
# all jobs charged before this time are included in legacy consumption.
CONSUMPTION_MIGRATED_AT = "consumption_migrated_at"


class ConsumptionMigrationError(Exception):
    """Raised when legacy consumption cannot be migrated to pairs."""


def _table_columns(cursor: sqlite3.Cursor, table: str) -> list[str]:
    cursor.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cursor.fetchall()]


def migrate_consumption_to_pairs(conn: sqlite3.Connection) -> None:
    """Move legacy per-user and per-account consumption to user/account pairs.

    Which user consumed on which account is unknown, so legacy consumption (in
    minutes) of each user is stored in seconds on the pair (user, UNATTRIBUTED)
    and legacy consumption of each account on the pair (UNATTRIBUTED, account),
    in user_account_consumption table. User and account totals are preserved
    exactly. UNATTRIBUTED user and account are created for this purpose, and the
    migration time is recorded for slurm-quota-reallocate.

    The consumption columns are then removed from users and accounts tables by
    rebuilding them, as ALTER TABLE DROP COLUMN is not available with
    SQLite < 3.35. Everything runs in a single transaction. Nothing is done when
    the users table has no legacy consumption column.
    """
    cursor = conn.cursor()
    if "total_consumed_cpu_minutes" not in _table_columns(cursor, "users"):
        logger.info("Migration not needed: consumption already stored per pair")
        return

    conn.commit()
    previous_isolation_level = conn.isolation_level
    conn.isolation_level = None  # explicit transaction control
    cursor.execute("PRAGMA foreign_keys = OFF")
    try:
        cursor.execute("BEGIN")

        for table, key in (("users", "username"), ("accounts", "account")):
            cursor.execute(f"SELECT 1 FROM {table} WHERE {key} = ?", (UNATTRIBUTED,))
            if cursor.fetchone() is not None:
                raise ConsumptionMigrationError(
                    f"{table[:-1].capitalize()} {UNATTRIBUTED} already exists, "
                    "this name is reserved to store unattributed consumption"
                )

        rows: list[tuple[str, str, int, int]] = []
        for table, key in (("users", "username"), ("accounts", "account")):
            cursor.execute(f"""
                SELECT {key}, COALESCE(total_consumed_cpu_minutes, 0),
                       COALESCE(total_consumed_gpu_minutes, 0)
                FROM {table}
                WHERE total_consumed_cpu_minutes > 0
                   OR total_consumed_gpu_minutes > 0
                ORDER BY {key}
            """)
            entries = cursor.fetchall()
            for name, cpu, gpu in entries:
                pair = (
                    (name, UNATTRIBUTED) if table == "users" else (UNATTRIBUTED, name)
                )
                rows.append((*pair, int(cpu) * 60, int(gpu) * 60))
            logger.info(
                "Legacy consumption of %d %s: %d CPU min, %d GPU min",
                len(entries),
                table,
                sum(int(cpu) for _, cpu, _ in entries),
                sum(int(gpu) for _, _, gpu in entries),
            )

        if rows:
            # Unlimited quotas, these entities are never submitted jobs.
            cursor.execute(
                "INSERT INTO users (username, quota_cpu_minutes, quota_gpu_minutes) "
                "VALUES (?, -1, -1)",
                (UNATTRIBUTED,),
            )
            cursor.execute(
                "INSERT INTO accounts (account, quota_cpu_minutes, quota_gpu_minutes) "
                "VALUES (?, -1, -1)",
                (UNATTRIBUTED,),
            )
            cursor.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (CONSUMPTION_MIGRATED_AT, str(int(time.time()))),
            )

        create_consumption_table(cursor)
        cursor.executemany(
            """
            INSERT INTO user_account_consumption (
                username, account, consumed_cpu_seconds, consumed_gpu_seconds
            )
            VALUES (?, ?, ?, ?)
            """,
            rows,
        )
        if rows:
            logger.info(
                "Stored legacy consumption on %d %s pairs, run "
                "slurm-quota-reallocate to attribute it to actual user/account "
                "pairs",
                len(rows),
                UNATTRIBUTED,
            )

        for table, key in (("users", "username"), ("accounts", "account")):
            cursor.execute(f"""
                CREATE TABLE {table}_new (
                    {key} TEXT PRIMARY KEY,
                    quota_cpu_minutes INTEGER DEFAULT -1,
                    quota_gpu_minutes INTEGER DEFAULT -1,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute(f"""
                INSERT INTO {table}_new (
                    {key}, quota_cpu_minutes, quota_gpu_minutes, last_updated
                )
                SELECT {key}, quota_cpu_minutes, quota_gpu_minutes, last_updated
                FROM {table}
            """)
            cursor.execute(f"DROP TABLE {table}")
            cursor.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
            logger.info("Removed consumption columns from %s table", table)

        cursor.execute("PRAGMA foreign_key_check")
        violations = cursor.fetchall()
        if violations:
            raise sqlite3.IntegrityError(
                f"Foreign key violations after migration: {violations}"
            )
        cursor.execute("COMMIT")
        logger.info("Migration completed: consumption stored per user/account pair")
    except BaseException:
        if conn.in_transaction:
            cursor.execute("ROLLBACK")
        raise
    finally:
        cursor.execute("PRAGMA foreign_keys = ON")
        conn.isolation_level = previous_isolation_level


def migrate_database() -> None:
    """
    Migrate existing database schema to add new columns if needed.
    This function is safe to call on databases that already have the columns.
    """
    if not os.path.exists(slurm_quota.DB_PATH):
        logger.error(f"Database file not found: {slurm_quota.DB_PATH}")
        sys.exit(1)

    try:
        with connect_database() as conn:
            cursor = conn.cursor()

            # Check if array_size column exists in jobs_preallocations
            cursor.execute("PRAGMA table_info(jobs_preallocations)")
            columns = [row[1] for row in cursor.fetchall()]

            # Add array_size column if it doesn't exist
            if "array_size" not in columns:
                logger.info("Adding array_size column to jobs_preallocations table")
                cursor.execute("""
                    ALTER TABLE jobs_preallocations
                    ADD COLUMN array_size INTEGER DEFAULT 1
                """)
                conn.commit()
                logger.info("Migration completed: array_size column added")
            else:
                logger.info("Migration not needed: array_size column already exists")

            # Check and add GPU columns to users table
            cursor.execute("PRAGMA table_info(users)")
            user_columns = [row[1] for row in cursor.fetchall()]
            # Consumption columns only exist in users and accounts tables before
            # consumption is moved to user/account pairs. Once moved, they must
            # not be added back.
            legacy_consumption = "total_consumed_cpu_minutes" in user_columns

            if legacy_consumption and "total_consumed_gpu_minutes" not in user_columns:
                logger.info("Adding total_consumed_gpu_minutes column to users table")
                cursor.execute("""
                    ALTER TABLE users
                    ADD COLUMN total_consumed_gpu_minutes INTEGER DEFAULT 0
                """)
                conn.commit()
                logger.info(
                    "Migration completed: total_consumed_gpu_minutes "
                    "column added to users"
                )

            if "quota_gpu_minutes" not in user_columns:
                logger.info("Adding quota_gpu_minutes column to users table")
                cursor.execute("""
                    ALTER TABLE users
                    ADD COLUMN quota_gpu_minutes INTEGER DEFAULT -1
                """)
                conn.commit()
                logger.info(
                    "Migration completed: quota_gpu_minutes column added to users"
                )

            # Check and add GPU columns to accounts table
            cursor.execute("PRAGMA table_info(accounts)")
            account_columns = [row[1] for row in cursor.fetchall()]

            if (
                legacy_consumption
                and "total_consumed_gpu_minutes" not in account_columns
            ):
                logger.info(
                    "Adding total_consumed_gpu_minutes column to accounts table"
                )
                cursor.execute("""
                    ALTER TABLE accounts
                    ADD COLUMN total_consumed_gpu_minutes INTEGER DEFAULT 0
                """)
                conn.commit()
                logger.info(
                    "Migration completed: total_consumed_gpu_minutes "
                    "column added to accounts"
                )

            if "quota_gpu_minutes" not in account_columns:
                logger.info("Adding quota_gpu_minutes column to accounts table")
                cursor.execute("""
                    ALTER TABLE accounts
                    ADD COLUMN quota_gpu_minutes INTEGER DEFAULT -1
                """)
                conn.commit()
                logger.info(
                    "Migration completed: quota_gpu_minutes column added to accounts"
                )

            # Check and add GPU column to jobs_preallocations table
            if "preallocated_gpu_minutes" not in columns:
                logger.info(
                    "Adding preallocated_gpu_minutes column to "
                    "jobs_preallocations table"
                )
                cursor.execute("""
                    ALTER TABLE jobs_preallocations
                    ADD COLUMN preallocated_gpu_minutes INTEGER DEFAULT 0
                """)
                conn.commit()
                logger.info(
                    "Migration completed: preallocated_gpu_minutes column "
                    "added to jobs_preallocations"
                )

            # Ensure gpu_factors table exists
            cursor.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='gpu_factors'"
            )
            if cursor.fetchone() is None:
                logger.info("Creating gpu_factors table")
                cursor.execute("""
                    CREATE TABLE gpu_factors (
                        gpu_type TEXT PRIMARY KEY,
                        factor REAL NOT NULL
                    )
                """)
                conn.commit()
                logger.info("Migration completed: gpu_factors table created")

            # Ensure settings table exists
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='settings'"
            )
            if cursor.fetchone() is None:
                logger.info("Creating settings table")
                cursor.execute("""
                    CREATE TABLE settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                """)
                conn.commit()
                logger.info("Migration completed: settings table created")

            # Ensure default quota settings are present
            for key, value in MIGRATE_DEFAULT_QUOTA_SETTINGS.items():
                cursor.execute(
                    """
                    INSERT INTO settings (key, value)
                    VALUES (?, ?)
                    ON CONFLICT(key) DO NOTHING
                    """,
                    (key, value),
                )
            conn.commit()
            logger.info("Migration completed: default quota settings ensured")

            # Ensure operators table exists
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='operators'"
            )
            if cursor.fetchone() is None:
                logger.info("Creating operators table")
                cursor.execute("""
                    CREATE TABLE operators (
                        username TEXT PRIMARY KEY
                    )
                """)
                conn.commit()
                logger.info("Migration completed: operators table created")

            # Ensure managers table exists
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='managers'"
            )
            if cursor.fetchone() is None:
                logger.info("Creating managers table")
                cursor.execute("""
                    CREATE TABLE managers (
                        username TEXT PRIMARY KEY
                    )
                """)
                conn.commit()
                logger.info("Migration completed: managers table created")

            # Ensure manager_accounts table exists
            cursor.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='manager_accounts'"
            )
            if cursor.fetchone() is None:
                logger.info("Creating manager_accounts table")
                cursor.execute("""
                    CREATE TABLE manager_accounts (
                        manager_username TEXT NOT NULL,
                        account TEXT NOT NULL,
                        PRIMARY KEY (manager_username, account),
                        FOREIGN KEY (manager_username) REFERENCES managers(username)
                        ON DELETE CASCADE
                    )
                """)
                conn.commit()
                logger.info("Migration completed: manager_accounts table created")

            # Move consumption to user/account pairs, in seconds (v4)
            migrate_consumption_to_pairs(conn)

            enable_wal_mode(conn)
            set_database_permissions()

    except sqlite3.Error as e:
        logger.error(f"Database migration failed: {e}")
        raise


def main() -> None:
    """Main entry point for the slurm-quota-migrate script."""
    parser = argparse.ArgumentParser(
        prog="slurm-quota-migrate",
        description="Migrate slurm-quota database schema",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    add_common_arguments(parser)

    args = parser.parse_args()

    setup_logging(args)

    current_user = get_current_user()
    if current_user != "root":
        logger.warning(
            "Migration script should be run as root user, currently running "
            f"as {current_user}"
        )

    try:
        migrate_database()
        logger.info("Database migration completed successfully")
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        sys.exit(1)
