# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""SQLite database schema and operations."""

import logging
import os
import pwd
import sqlite3
from typing import Any, Optional

import slurm_quota
from slurm_quota import auth, slurm as slurm_integration

logger = logging.getLogger("slurm_quota")

DEFAULT_QUOTA_SETTINGS = {
    "default_user_quota_cpu_minutes": -1,
    "default_user_quota_gpu_minutes": -1,
    "default_account_quota_cpu_minutes": -1,
    "default_account_quota_gpu_minutes": -1,
}


def configure_connection(conn: sqlite3.Connection) -> None:
    """Apply per-connection SQLite settings for slurm-quota.

    busy_timeout makes SQLite wait up to five seconds when the database is
    locked instead of failing immediately. foreign_keys turns on enforcement of
    foreign key constraints defined in the schema. Both settings must be applied
    on every new connection because they are not persisted in the database file.
    """
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")


def connect_database() -> sqlite3.Connection:
    """Open a connection to the slurm-quota database with standard settings.

    Returns a sqlite3 connection to DB_PATH after applying configure_connection.
    Callers should use this helper instead of sqlite3.connect directly so that
    busy_timeout and foreign_keys are always enabled.
    """
    conn = sqlite3.connect(slurm_quota.DB_PATH)
    configure_connection(conn)
    return conn


def enable_wal_mode(conn: sqlite3.Connection) -> None:
    """Switch the database to WAL journal mode if it is not already active.

    WAL reduces lock contention between concurrent writers such as job_submit.lua
    and slurm-quota-serve. Unlike busy_timeout and foreign_keys, journal_mode is
    stored in the database file and only needs to be set once per database. The
    caller must not be inside an open transaction; this function commits when done.
    """
    row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
    if not row or row[0].lower() != "wal":
        logger.warning(
            "Failed to enable WAL journal mode: got %r", row[0] if row else None
        )
    conn.commit()


def get_default_quota_settings(conn: sqlite3.Connection) -> dict[str, int]:
    """
    Return configured default quotas used for new users/accounts.

    Settings are read through conn. Missing or invalid values fall back to -1
    (unlimited) for robustness.
    """
    defaults = dict(DEFAULT_QUOTA_SETTINGS)

    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT key, value FROM settings WHERE key IN (?, ?, ?, ?)",
            tuple(DEFAULT_QUOTA_SETTINGS.keys()),
        )
        for key, value in cursor.fetchall():
            if key in defaults:
                try:
                    defaults[key] = int(value)
                except (TypeError, ValueError):
                    logger.warning(
                        "Invalid default quota setting for %s=%r; using %d",
                        key,
                        value,
                        DEFAULT_QUOTA_SETTINGS[key],
                    )
    except sqlite3.Error as e:
        logger.warning("Failed to load default quota settings from database: %s", e)

    return defaults


def set_default_quota_settings(
    default_user_cpu: Optional[int],
    default_user_gpu: Optional[int],
    default_account_cpu: Optional[int],
    default_account_gpu: Optional[int],
) -> None:
    """
    Set one or more default quotas used for newly auto-created entities.
    """
    updates: dict[str, int] = {}
    if default_user_cpu is not None:
        updates["default_user_quota_cpu_minutes"] = default_user_cpu
    if default_user_gpu is not None:
        updates["default_user_quota_gpu_minutes"] = default_user_gpu
    if default_account_cpu is not None:
        updates["default_account_quota_cpu_minutes"] = default_account_cpu
    if default_account_gpu is not None:
        updates["default_account_quota_gpu_minutes"] = default_account_gpu

    if not updates:
        raise ValueError("No default quotas provided")

    with connect_database() as conn:
        cursor = conn.cursor()
        cursor.executemany(
            """
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            [(key, str(value)) for key, value in updates.items()],
        )
        conn.commit()


def load_gpu_factors() -> dict[str, float]:
    """
    Load GPU type charging factors from the SQLite database.

    The factors are stored in the gpu_factors table with:
      - gpu_type TEXT PRIMARY KEY
      - factor   REAL NOT NULL

    A special gpu_type value of default controls the default factor used
    when no explicit entry exists for a given GPU type.

    Returns:
        Dictionary mapping GPU type to factor. The key __default__ holds
        the default factor (1.0 when not configured).
    """
    factors: dict[str, float] = {}
    default_factor = 1.0

    if not os.path.exists(slurm_quota.DB_PATH):
        # No database yet – use built-in defaults
        factors["__default__"] = default_factor
        return factors

    try:
        with connect_database() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT gpu_type, factor FROM gpu_factors")
            rows = cursor.fetchall()

            for gpu_type, factor in rows:
                try:
                    factor_f = float(factor)
                except (TypeError, ValueError):
                    logger.warning(
                        "Invalid GPU factor value for type %r: %r; skipping",
                        gpu_type,
                        factor,
                    )
                    continue

                if gpu_type == "default":
                    if factor_f < 0:
                        logger.warning(
                            "Invalid default GPU factor %r; keeping 1.0", factor_f
                        )
                    else:
                        default_factor = factor_f
                else:
                    if factor_f < 0:
                        logger.warning(
                            "Invalid GPU factor %r for type %r; skipping",
                            factor_f,
                            gpu_type,
                        )
                        continue
                    factors[str(gpu_type)] = factor_f
    except sqlite3.Error as e:
        logger.warning("Failed to load GPU factors from database: %s", e)

    factors["__default__"] = default_factor
    return factors


def set_gpu_factor(gpu_type: str, factor: float) -> None:
    """
    Set charging factor for a GPU type in the database.

    Args:
        gpu_type: GPU type name (use "default" for the default factor)
        factor: Charging factor (float, must be non-negative)
    """
    try:
        with connect_database() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT INTO gpu_factors (gpu_type, factor)
                VALUES (?, ?)
                ON CONFLICT(gpu_type) DO UPDATE SET factor = excluded.factor
                """,
                (gpu_type, float(factor)),
            )

            conn.commit()

    except sqlite3.Error as e:
        logger.error(f"Failed to set GPU factor: {e}")
        raise


def init_database() -> None:
    """
    Initialize the SQLite database only if it doesn't exist yet. Set permissions on
    first creation. Ensure WAL mode is enabled on every call.
    """
    try:
        # Ensure the directory exists
        os.makedirs(os.path.dirname(slurm_quota.DB_PATH), exist_ok=True)

        # If the database already exists, enable WAL mode and make sure permissions
        # are set correctly.
        if os.path.exists(slurm_quota.DB_PATH):
            with connect_database() as conn:
                enable_wal_mode(conn)
            set_database_permissions()
            return

        with connect_database() as conn:
            cursor = conn.cursor()

            # Create users table to track resource usage
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    total_consumed_cpu_minutes INTEGER DEFAULT 0,
                    quota_cpu_minutes INTEGER DEFAULT -1,
                    total_consumed_gpu_minutes INTEGER DEFAULT 0,
                    quota_gpu_minutes INTEGER DEFAULT -1,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create accounts table to track resource usage
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    account TEXT PRIMARY KEY,
                    total_consumed_cpu_minutes INTEGER DEFAULT 0,
                    quota_cpu_minutes INTEGER DEFAULT -1,
                    total_consumed_gpu_minutes INTEGER DEFAULT 0,
                    quota_gpu_minutes INTEGER DEFAULT -1,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create jobs_preallocations table to track preallocated resources per job
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS jobs_preallocations (
                    job_uuid TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    account TEXT NOT NULL,
                    preallocated_cpu_minutes INTEGER NOT NULL,
                    preallocated_gpu_minutes INTEGER DEFAULT 0,
                    array_size INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (username) REFERENCES users (username),
                    FOREIGN KEY (account) REFERENCES accounts (account)
                )
            """)

            # Create gpu_factors table to store GPU charging factors
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS gpu_factors (
                    gpu_type TEXT PRIMARY KEY,
                    factor REAL NOT NULL
                )
            """)

            # Create settings table for runtime configuration values
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            cursor.executemany(
                """
                INSERT INTO settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO NOTHING
                """,
                [(key, str(value)) for key, value in DEFAULT_QUOTA_SETTINGS.items()],
            )

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS operators (
                    username TEXT PRIMARY KEY
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS managers (
                    username TEXT PRIMARY KEY
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS manager_accounts (
                    manager_username TEXT NOT NULL,
                    account TEXT NOT NULL,
                    PRIMARY KEY (manager_username, account),
                    FOREIGN KEY (manager_username) REFERENCES managers(username)
                    ON DELETE CASCADE
                )
            """)

            conn.commit()
            enable_wal_mode(conn)
            logger.info("Database initialized successfully")

        # Set permissions on the database file
        set_database_permissions()

    except sqlite3.Error as e:
        logger.error(f"Database initialization failed: {e}")
        raise
    except OSError as e:
        logger.error(f"Failed to create database directory: {e}")
        raise


def set_database_permissions() -> None:
    """
    Set database file permissions to allow root and slurm users to modify it and
    others to read it.
    """
    paths = [slurm_quota.DB_PATH]
    for suffix in ("-wal", "-shm"):
        wal_path = slurm_quota.DB_PATH + suffix
        if os.path.exists(wal_path):
            paths.append(wal_path)

    try:
        slurm_pw = None
        if auth.get_current_user() == "root":
            slurm_pw = pwd.getpwnam("slurm")

        for path in paths:
            if slurm_pw is not None:
                logger.debug("Giving database file to slurm user for %s", path)
                os.chown(path, slurm_pw.pw_uid, slurm_pw.pw_gid)
            os.chmod(path, 0o644)
            logger.debug("Database permissions set for %s", path)
    except OSError as e:
        logger.error(f"Failed to set database permissions: {e}")
        raise


def prune_resources(
    targets: set[str],
    dry_run: bool = False,
    user_filter: Optional[str] = None,
    account_filter: Optional[str] = None,
) -> dict[str, int]:
    """
    Prune selected resource records from the database.

    Args:
        targets: Set of prune targets among {"preallocs", "users", "accounts"}.
        dry_run: If True, report counts without deleting rows.
        user_filter: Optional username to limit user pruning candidates.
        account_filter: Optional account to limit account pruning candidates.

    Returns:
        Mapping with deleted (or deletable in dry-run) rows per target.
    """
    counts = {"preallocs": 0, "users": 0, "accounts": 0}
    if not os.path.exists(slurm_quota.DB_PATH):
        logger.info("Database not found; nothing to prune")
        return counts

    orphan_prealloc_uuids: list[str] = []
    if "preallocs" in targets:
        with connect_database() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT job_uuid, username, account FROM jobs_preallocations"
            )
            prealloc_rows = cursor.fetchall()
        active_job_uuids = slurm_integration.collect_active_job_uuids()
        orphan_prealloc_uuids = [
            str(job_uuid)
            for job_uuid, _username, _account in prealloc_rows
            if job_uuid and job_uuid not in active_job_uuids
        ]
        counts["preallocs"] = len(orphan_prealloc_uuids)

    users_to_delete: list[str] = []
    if "users" in targets:
        with connect_database() as conn:
            cursor = conn.cursor()
            users_query = """
                SELECT username
                FROM users
                WHERE total_consumed_cpu_minutes = 0
                  AND total_consumed_gpu_minutes = 0
            """
            users_params: list[str] = []
            if user_filter:
                users_query += " AND username = ?"
                users_params.append(user_filter)
            cursor.execute(users_query, users_params)
            users_to_delete = [
                str(username) for (username,) in cursor.fetchall() if username
            ]
            for username in users_to_delete:
                logger.info("Eligible user for pruning: %s", username)
            counts["users"] = len(users_to_delete)

    accounts_to_delete: list[str] = []
    if "accounts" in targets:
        with connect_database() as conn:
            cursor = conn.cursor()
            accounts_query = """
                SELECT account
                FROM accounts
                WHERE total_consumed_cpu_minutes = 0
                  AND total_consumed_gpu_minutes = 0
            """
            accounts_params: list[str] = []
            if account_filter:
                accounts_query += " AND account = ?"
                accounts_params.append(account_filter)
            cursor.execute(accounts_query, accounts_params)
            accounts_to_delete = [
                str(account) for (account,) in cursor.fetchall() if account
            ]
            for account in accounts_to_delete:
                logger.info("Eligible account for pruning: %s", account)
            counts["accounts"] = len(accounts_to_delete)

    if dry_run or (
        not orphan_prealloc_uuids and not users_to_delete and not accounts_to_delete
    ):
        return counts

    # Delete preallocations first so users/accounts can become eligible in the same run.
    with connect_database() as conn:
        cursor = conn.cursor()

        if orphan_prealloc_uuids:
            cursor.executemany(
                "DELETE FROM jobs_preallocations WHERE job_uuid = ?",
                [(job_uuid,) for job_uuid in orphan_prealloc_uuids],
            )

        if users_to_delete:
            try:
                cursor.executemany(
                    "DELETE FROM users WHERE username = ?",
                    [(username,) for username in users_to_delete],
                )
            except sqlite3.IntegrityError as e:
                raise sqlite3.IntegrityError(
                    "Failed to prune users: one or more users are still referenced "
                    "by jobs preallocations"
                ) from e

        if accounts_to_delete:
            try:
                cursor.executemany(
                    "DELETE FROM accounts WHERE account = ?",
                    [(account,) for account in accounts_to_delete],
                )
            except sqlite3.IntegrityError as e:
                raise sqlite3.IntegrityError(
                    "Failed to prune accounts: one or more accounts are still "
                    "referenced by jobs preallocations"
                ) from e

        conn.commit()

    return counts


def update_user_and_account_resources(
    username: str,
    account: str,
    consumed_cpu_minutes: int,
    job_uuid: Optional[str],
    consumed_gpu_minutes: int = 0,
) -> str:
    """
    Update user and account resource consumption in the database and adjust any
    preallocation associated with the job UUID.

    Args:
        username: The username of the job owner
        account: The account of the job
        consumed_cpu_minutes: Actual CPU minutes consumed by the job
        job_uuid: The job UUID from admin_comment
        consumed_gpu_minutes: Actual GPU minutes consumed by the job
    Returns:
        One of:
        - "removed": a preallocation row was fully removed
        - "decremented": array_size was decremented but row kept
        - "none": no preallocation was changed (including when job_uuid is None)
    """
    try:
        with connect_database() as conn:
            cursor = conn.cursor()
            dq = get_default_quota_settings(conn)
            default_user_cpu = dq["default_user_quota_cpu_minutes"]
            default_user_gpu = dq["default_user_quota_gpu_minutes"]
            default_account_cpu = dq["default_account_quota_cpu_minutes"]
            default_account_gpu = dq["default_account_quota_gpu_minutes"]

            # Update consumed CPU and GPU minutes for user
            cursor.execute(
                """
                INSERT INTO users (
                    username, total_consumed_cpu_minutes, quota_cpu_minutes,
                    total_consumed_gpu_minutes, quota_gpu_minutes, last_updated
                )
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT (username) DO
                UPDATE SET total_consumed_cpu_minutes = total_consumed_cpu_minutes + ?,
                           total_consumed_gpu_minutes = total_consumed_gpu_minutes + ?,
                           last_updated = CURRENT_TIMESTAMP
                """,
                (
                    username,
                    consumed_cpu_minutes,
                    default_user_cpu,
                    consumed_gpu_minutes,
                    default_user_gpu,
                    consumed_cpu_minutes,
                    consumed_gpu_minutes,
                ),
            )

            # Update consumed CPU and GPU minutes for account
            cursor.execute(
                """
                INSERT INTO accounts (
                    account, total_consumed_cpu_minutes, quota_cpu_minutes,
                    total_consumed_gpu_minutes, quota_gpu_minutes, last_updated
                )
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT (account) DO
                UPDATE SET total_consumed_cpu_minutes = total_consumed_cpu_minutes + ?,
                           total_consumed_gpu_minutes = total_consumed_gpu_minutes + ?,
                            last_updated = CURRENT_TIMESTAMP
                """,
                (
                    account,
                    consumed_cpu_minutes,
                    default_account_cpu,
                    consumed_gpu_minutes,
                    default_account_gpu,
                    consumed_cpu_minutes,
                    consumed_gpu_minutes,
                ),
            )

            # Update or remove preallocation if job_uuid exists
            if job_uuid:
                prealloc_status = "none"
                # Fetch current array_size for this job_uuid
                cursor.execute(
                    "SELECT array_size FROM jobs_preallocations WHERE job_uuid = ?",
                    (job_uuid,),
                )
                row = cursor.fetchone()
                if row is None:
                    logger.warning(
                        f"No preallocated resources found for job UUID: {job_uuid}"
                    )
                else:
                    current_array_size = int(row[0] or 0)
                    if current_array_size <= 1:
                        # Last element of the array: remove preallocation entirely
                        cursor.execute(
                            "DELETE FROM jobs_preallocations WHERE job_uuid = ?",
                            (job_uuid,),
                        )
                        if (cursor.rowcount or 0) > 0:
                            logger.debug(
                                "Removed preallocated resources for job UUID: %s",
                                job_uuid,
                            )
                            prealloc_status = "removed"
                    else:
                        # Decrement remaining array_size
                        new_array_size = current_array_size - 1
                        cursor.execute(
                            """
                            UPDATE jobs_preallocations
                            SET array_size = ?
                            WHERE job_uuid = ?
                            """,
                            (new_array_size, job_uuid),
                        )
                        logger.debug(
                            "Decremented preallocation array_size "
                            "for job UUID %s: %d -> %d",
                            job_uuid,
                            current_array_size,
                            new_array_size,
                        )
                        prealloc_status = "decremented"
            else:
                prealloc_status = "none"

            conn.commit()

            return prealloc_status

    except sqlite3.Error as e:
        logger.error(f"Failed to update user resources: {e}")
        raise


def set_user_quota(username: str, quota_cpu_minutes: int) -> None:
    """
    Set quota for a user in the database.

    Args:
        username: The username to set quota for
        quota_cpu_minutes: The quota in CPU minutes (-1 for unlimited)
    """
    try:
        with connect_database() as conn:
            cursor = conn.cursor()
            dq = get_default_quota_settings(conn)
            default_gpu = dq["default_user_quota_gpu_minutes"]

            # Insert or update user with quota
            cursor.execute(
                """
                INSERT INTO users (
                    username, quota_cpu_minutes, quota_gpu_minutes, last_updated
                )
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT (username) DO
                UPDATE SET quota_cpu_minutes = ?, last_updated = CURRENT_TIMESTAMP
                """,
                (username, quota_cpu_minutes, default_gpu, quota_cpu_minutes),
            )

            conn.commit()

    except sqlite3.Error as e:
        logger.error(f"Failed to set user quota: {e}")
        raise


def set_account_quota(account: str, quota_cpu_minutes: int) -> None:
    """
    Set quota for an account in the database.

    Args:
        account: The account to set quota for
        quota_cpu_minutes: The quota in CPU minutes (-1 for unlimited)
    """
    try:
        with connect_database() as conn:
            cursor = conn.cursor()
            dq = get_default_quota_settings(conn)
            default_gpu = dq["default_account_quota_gpu_minutes"]

            cursor.execute(
                """
                INSERT INTO accounts (
                    account, quota_cpu_minutes, quota_gpu_minutes, last_updated
                )
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT (account) DO
                UPDATE SET quota_cpu_minutes = ?, last_updated = CURRENT_TIMESTAMP
                """,
                (account, quota_cpu_minutes, default_gpu, quota_cpu_minutes),
            )

            conn.commit()

    except sqlite3.Error as e:
        logger.error(f"Failed to set account quota: {e}")
        raise


def set_user_gpu_quota(username: str, quota_gpu_minutes: int) -> None:
    """
    Set GPU quota for a user in the database.

    Args:
        username: The username to set GPU quota for
        quota_gpu_minutes: The quota in GPU minutes (-1 for unlimited)
    """
    try:
        with connect_database() as conn:
            cursor = conn.cursor()
            dq = get_default_quota_settings(conn)
            default_cpu = dq["default_user_quota_cpu_minutes"]

            # Insert or update user with GPU quota
            cursor.execute(
                """
                INSERT INTO users (
                    username, quota_cpu_minutes, quota_gpu_minutes, last_updated
                )
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT (username) DO
                UPDATE SET quota_gpu_minutes = ?, last_updated = CURRENT_TIMESTAMP
                """,
                (username, default_cpu, quota_gpu_minutes, quota_gpu_minutes),
            )

            conn.commit()

    except sqlite3.Error as e:
        logger.error(f"Failed to set user GPU quota: {e}")
        raise


def set_account_gpu_quota(account: str, quota_gpu_minutes: int) -> None:
    """
    Set GPU quota for an account in the database.

    Args:
        account: The account to set GPU quota for
        quota_gpu_minutes: The quota in GPU minutes (-1 for unlimited)
    """
    try:
        with connect_database() as conn:
            cursor = conn.cursor()
            dq = get_default_quota_settings(conn)
            default_cpu = dq["default_account_quota_cpu_minutes"]

            cursor.execute(
                """
                INSERT INTO accounts (
                    account, quota_cpu_minutes, quota_gpu_minutes, last_updated
                )
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT (account) DO
                UPDATE SET quota_gpu_minutes = ?, last_updated = CURRENT_TIMESTAMP
                """,
                (account, default_cpu, quota_gpu_minutes, quota_gpu_minutes),
            )

            conn.commit()

    except sqlite3.Error as e:
        logger.error(f"Failed to set account GPU quota: {e}")
        raise


def adjust_consumed_minutes(
    target_type: str, target_name: str, resource: str, delta_minutes: int
) -> int:
    """
    Adjust consumed CPU/GPU minutes for a user or account.

    Args:
        target_type: "user" or "account"
        target_name: Username or account name
        resource: "cpu" or "gpu"
        delta_minutes: Signed minutes to add/subtract

    Returns:
        New consumed minutes value after update

    Raises:
        ValueError: If target type/resource is invalid or target row is missing
    """
    target_mapping = {
        "user": ("users", "username", "User"),
        "account": ("accounts", "account", "Account"),
    }
    resource_mapping = {
        "cpu": "total_consumed_cpu_minutes",
        "gpu": "total_consumed_gpu_minutes",
    }
    if target_type not in target_mapping:
        raise ValueError(f"Invalid target type: {target_type}")
    if resource not in resource_mapping:
        raise ValueError(f"Invalid resource type: {resource}")

    table_name, id_column, target_label = target_mapping[target_type]
    consumed_column = resource_mapping[resource]

    with connect_database() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {consumed_column} FROM {table_name} WHERE {id_column} = ?",
            (target_name,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError(f"{target_label} not found: {target_name}")

        current_value = int(row[0] or 0)
        updated_value = max(0, current_value + delta_minutes)
        cursor.execute(
            (
                f"UPDATE {table_name} "
                f"SET {consumed_column} = ?, last_updated = CURRENT_TIMESTAMP "
                f"WHERE {id_column} = ?"
            ),
            (updated_value, target_name),
        )
        conn.commit()
        return updated_value


def query_users_aggregate(
    conn: sqlite3.Connection,
    usernames: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    cursor = conn.cursor()
    if usernames is not None:
        if len(usernames) == 0:
            return []
        placeholders = ",".join(["?"] * len(usernames))
        cursor.execute(
            f"""
            SELECT u.username, u.total_consumed_cpu_minutes,
                   COALESCE(
                       SUM(j.preallocated_cpu_minutes * COALESCE(j.array_size, 1)),
                       0
                   ) as total_preallocated_cpu_minutes,
                   u.quota_cpu_minutes, u.total_consumed_gpu_minutes,
                   COALESCE(
                       SUM(j.preallocated_gpu_minutes * COALESCE(j.array_size, 1)),
                       0
                   ) as total_preallocated_gpu_minutes,
                   u.quota_gpu_minutes, u.last_updated,
                   COALESCE(SUM(COALESCE(j.array_size, 0)), 0) as job_count
            FROM users u
            LEFT JOIN jobs_preallocations j ON u.username = j.username
            WHERE u.username IN ({placeholders})
            GROUP BY u.username, u.total_consumed_cpu_minutes, u.quota_cpu_minutes,
                     u.total_consumed_gpu_minutes, u.quota_gpu_minutes, u.last_updated
            ORDER BY u.username
            """,
            tuple(usernames),
        )
    else:
        cursor.execute(
            """
            SELECT u.username, u.total_consumed_cpu_minutes,
                   COALESCE(
                       SUM(j.preallocated_cpu_minutes * COALESCE(j.array_size, 1)),
                       0
                   ) as total_preallocated_cpu_minutes,
                   u.quota_cpu_minutes, u.total_consumed_gpu_minutes,
                   COALESCE(
                       SUM(j.preallocated_gpu_minutes * COALESCE(j.array_size, 1)),
                       0
                   ) as total_preallocated_gpu_minutes,
                   u.quota_gpu_minutes, u.last_updated,
                   COALESCE(SUM(COALESCE(j.array_size, 0)), 0) as job_count
            FROM users u
            LEFT JOIN jobs_preallocations j ON u.username = j.username
            GROUP BY u.username, u.total_consumed_cpu_minutes, u.quota_cpu_minutes,
                     u.total_consumed_gpu_minutes, u.quota_gpu_minutes, u.last_updated
            ORDER BY u.username
            """
        )
    rows = cursor.fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        (
            uname,
            consumed_cpu,
            prealloc_cpu,
            quota_cpu,
            consumed_gpu,
            prealloc_gpu,
            quota_gpu,
            last_updated,
            job_count,
        ) = row
        results.append(
            {
                "username": uname,
                "total_consumed_cpu_minutes": int(consumed_cpu or 0),
                "total_preallocated_cpu_minutes": int(prealloc_cpu or 0),
                "quota_cpu_minutes": int(quota_cpu if quota_cpu is not None else -1),
                "total_consumed_gpu_minutes": int(consumed_gpu or 0),
                "total_preallocated_gpu_minutes": int(prealloc_gpu or 0),
                "quota_gpu_minutes": int(quota_gpu if quota_gpu is not None else -1),
                "last_updated": last_updated,
                "job_count": int(job_count or 0),
            }
        )
    return results


def query_accounts_aggregate(
    conn: sqlite3.Connection, accounts_filter: Optional[set[str]] = None
) -> list[dict[str, Any]]:
    cursor = conn.cursor()
    if accounts_filter is not None:
        if len(accounts_filter) == 0:
            return []
        placeholders = ",".join(["?"] * len(accounts_filter))
        cursor.execute(
            f"""
            SELECT a.account, a.total_consumed_cpu_minutes,
                   COALESCE(
                       SUM(j.preallocated_cpu_minutes * COALESCE(j.array_size, 1)),
                       0
                   ) as total_preallocated_cpu_minutes,
                   a.quota_cpu_minutes, a.total_consumed_gpu_minutes,
                   COALESCE(
                       SUM(j.preallocated_gpu_minutes * COALESCE(j.array_size, 1)),
                       0
                   ) as total_preallocated_gpu_minutes,
                   a.quota_gpu_minutes, a.last_updated,
                   COALESCE(SUM(COALESCE(j.array_size, 0)), 0) as job_count
            FROM accounts a
            LEFT JOIN jobs_preallocations j ON a.account = j.account
            WHERE a.account IN ({placeholders})
            GROUP BY a.account, a.total_consumed_cpu_minutes, a.quota_cpu_minutes,
                     a.total_consumed_gpu_minutes, a.quota_gpu_minutes, a.last_updated
            ORDER BY a.account
            """,
            tuple(accounts_filter),
        )
    else:
        cursor.execute(
            """
            SELECT a.account, a.total_consumed_cpu_minutes,
                   COALESCE(
                       SUM(j.preallocated_cpu_minutes * COALESCE(j.array_size, 1)),
                       0
                   ) as total_preallocated_cpu_minutes,
                   a.quota_cpu_minutes, a.total_consumed_gpu_minutes,
                   COALESCE(
                       SUM(j.preallocated_gpu_minutes * COALESCE(j.array_size, 1)),
                       0
                   ) as total_preallocated_gpu_minutes,
                   a.quota_gpu_minutes, a.last_updated,
                   COALESCE(SUM(COALESCE(j.array_size, 0)), 0) as job_count
            FROM accounts a
            LEFT JOIN jobs_preallocations j ON a.account = j.account
            GROUP BY a.account, a.total_consumed_cpu_minutes, a.quota_cpu_minutes,
                     a.total_consumed_gpu_minutes, a.quota_gpu_minutes, a.last_updated
            ORDER BY a.account
            """
        )
    rows = cursor.fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        (
            account,
            consumed_cpu,
            prealloc_cpu,
            quota_cpu,
            consumed_gpu,
            prealloc_gpu,
            quota_gpu,
            last_updated,
            job_count,
        ) = row
        results.append(
            {
                "account": account,
                "total_consumed_cpu_minutes": int(consumed_cpu or 0),
                "total_preallocated_cpu_minutes": int(prealloc_cpu or 0),
                "quota_cpu_minutes": int(quota_cpu if quota_cpu is not None else -1),
                "total_consumed_gpu_minutes": int(consumed_gpu or 0),
                "total_preallocated_gpu_minutes": int(prealloc_gpu or 0),
                "quota_gpu_minutes": int(quota_gpu if quota_gpu is not None else -1),
                "last_updated": last_updated,
                "job_count": int(job_count or 0),
            }
        )
    return results


def is_operator(conn: sqlite3.Connection, username: str) -> bool:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM operators WHERE username = ?",
        (username,),
    )
    return cursor.fetchone() is not None


def list_operators(conn: sqlite3.Connection) -> list[str]:
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM operators ORDER BY username")
    return [row[0] for row in cursor.fetchall()]


def grant_operator(conn: sqlite3.Connection, username: str) -> None:
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO operators (username) VALUES (?)",
        (username,),
    )
    conn.commit()


def revoke_operator(conn: sqlite3.Connection, username: str) -> None:
    cursor = conn.cursor()
    cursor.execute("DELETE FROM operators WHERE username = ?", (username,))
    conn.commit()


def is_manager(conn: sqlite3.Connection, username: str) -> bool:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM managers WHERE username = ?",
        (username,),
    )
    return cursor.fetchone() is not None


def list_managers(conn: sqlite3.Connection) -> list[str]:
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM managers ORDER BY username")
    return [row[0] for row in cursor.fetchall()]


def grant_manager(conn: sqlite3.Connection, username: str) -> None:
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO managers (username) VALUES (?)",
        (username,),
    )
    conn.commit()


def revoke_manager(conn: sqlite3.Connection, username: str) -> None:
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM manager_accounts WHERE manager_username = ?",
        (username,),
    )
    cursor.execute("DELETE FROM managers WHERE username = ?", (username,))
    conn.commit()


def list_manager_accounts(conn: sqlite3.Connection, username: str) -> list[str]:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT account FROM manager_accounts
        WHERE manager_username = ?
        ORDER BY account
        """,
        (username,),
    )
    return [row[0] for row in cursor.fetchall()]


def grant_manager_account(
    conn: sqlite3.Connection, username: str, account: str
) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR IGNORE INTO manager_accounts (manager_username, account)
        VALUES (?, ?)
        """,
        (username, account),
    )
    conn.commit()


def revoke_manager_account(
    conn: sqlite3.Connection, username: str, account: str
) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        DELETE FROM manager_accounts
        WHERE manager_username = ? AND account = ?
        """,
        (username, account),
    )
    conn.commit()


def list_all_manager_accounts(conn: sqlite3.Connection) -> dict[str, list[str]]:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT manager_username, account FROM manager_accounts
        ORDER BY manager_username, account
        """
    )
    result: dict[str, list[str]] = {}
    for manager_username, account in cursor.fetchall():
        result.setdefault(manager_username, []).append(account)
    return result


def list_users_with_roles(
    conn: sqlite3.Connection, config_admins: set[str]
) -> list[dict[str, Any]]:
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM users")
    usernames = {row[0] for row in cursor.fetchall()}
    usernames.update(config_admins)
    operators = set(list_operators(conn))
    managers = set(list_managers(conn))
    usernames.update(operators)
    usernames.update(managers)
    manager_accounts = list_all_manager_accounts(conn)

    results: list[dict[str, Any]] = []
    for username in sorted(usernames):
        if username in config_admins:
            role = "admin"
        elif username in operators:
            role = "operator"
        elif username in managers:
            role = "manager"
        else:
            role = "user"
        entry: dict[str, Any] = {"username": username, "role": role}
        if role == "manager":
            entry["accounts"] = manager_accounts.get(username, [])
        results.append(entry)
    return results
