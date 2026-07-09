# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Database migration CLI for slurm-quota."""

import argparse
import os
import sqlite3
import sys

import slurm_quota
from slurm_quota.auth import get_current_user
from slurm_quota.cli import add_common_arguments
from slurm_quota.database import (
    connect_database,
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

            if "total_consumed_gpu_minutes" not in user_columns:
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

            if "total_consumed_gpu_minutes" not in account_columns:
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
