# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""CLI command implementations."""

import getpass
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import URLError

from slurm_quota import auth
import slurm_quota
from slurm_quota.client import APIClient, ServiceHTTPError
from slurm_quota.token import load_service_token, save_service_token
from slurm_quota.database import (
    adjust_consumed_minutes,
    get_default_quota_settings,
    init_database,
    prune_resources,
    set_account_gpu_quota,
    set_account_quota,
    set_default_quota_settings,
    set_gpu_factor,
    set_user_gpu_quota,
    set_user_quota,
)

import logging

logger = logging.getLogger("slurm_quota")


def format_timestamp_with_timezone(timestamp_str: Optional[str]) -> str:
    """
    Format a timestamp string to display with local timezone.

    Args:
        timestamp_str: UTC timestamp string from REST API in ISO format

    Returns:
        Formatted timestamp string with local timezone, or "N/A" if invalid
    """
    if not timestamp_str:
        return "N/A"

    try:
        # ISO format from API without timezone - assume UTC
        dt = datetime.fromisoformat(timestamp_str)
        # Replace timezone info to treat as UTC
        dt = dt.replace(tzinfo=timezone.utc)

        # Convert to local timezone
        local_dt = dt.astimezone()
        # Format as YYYY-MM-DD HH:MM:SS TZ
        return local_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    except (ValueError, TypeError) as e:
        logger.debug(f"Failed to parse timestamp '{timestamp_str}': {e}")
        return "N/A"


def create_status_bar(used: int, total: int, width: int = 20) -> str:
    """
    Create a visual status bar representing quota usage.

    Args:
        used: Used amount
        total: Total quota amount
        width: Width of the status bar in characters

    Returns:
        String representation of the status bar
    """
    if total <= 0:
        return " " * width

    percentage = min(used / total, 1.0)
    filled = int(percentage * width)
    bar = "█" * filled + "░" * (width - filled)

    # Apply color based on thresholds unless NO_COLOR is set
    if os.environ.get("NO_COLOR") is None:
        # >95% -> red, >80% -> orange/yellow, else green
        if percentage > 0.95:
            color_start = "\033[31m"  # red
        elif percentage > 0.80:
            # Use 256-color orange when supported; many terminals treat as orange
            color_start = "\033[38;5;208m"  # orange
        else:
            color_start = "\033[32m"  # green
        color_end = "\033[0m"
        colored_bar = f"{color_start}{bar}{color_end}"
    else:
        colored_bar = bar

    return f"[{colored_bar}] {percentage:6.1%}"


def set_user_quota_command(username: str, quota: int) -> None:
    """
    Execute the set-quota command to set user quota.
    This command can only be executed by root user.
    """
    try:
        # Check if running as root user
        current_user = auth.get_current_user()
        if current_user != "root":
            logger.error(
                f"Set-quota command can only be executed by root user, not by "
                f"{current_user}"
            )
            sys.exit(1)

        # Initialize database only if missing
        init_database()

        # Set user quota
        set_user_quota(username, quota)

        print(f"Successfully set quota for user {username}: {quota} CPU minutes")

    except Exception as e:
        logger.error(f"User-quota command failed: {e}")
        sys.exit(1)


def set_account_quota_command(account: str, quota: int) -> None:
    """
    Execute the account-quota command to set account quota.
    This command can only be executed by root user.
    """
    try:
        # Check if running as root user
        current_user = auth.get_current_user()
        if current_user != "root":
            logger.error(
                f"Account-quota command can only be executed by root user, not by "
                f"{current_user}"
            )
            sys.exit(1)

        # Initialize database only if missing
        init_database()

        # Set account quota
        set_account_quota(account, quota)

        print(f"Successfully set quota for account {account}: {quota} CPU minutes")

    except Exception as e:
        logger.error(f"Account-quota command failed: {e}")
        sys.exit(1)


def set_user_gpu_quota_command(username: str, quota: int) -> None:
    """
    Execute the user-gpu-quota command to set user GPU quota.
    This command can only be executed by root user.
    """
    try:
        # Check if running as root user
        current_user = auth.get_current_user()
        if current_user != "root":
            logger.error(
                f"User-gpu-quota command can only be executed by root user, not by "
                f"{current_user}"
            )
            sys.exit(1)

        # Initialize database only if missing
        init_database()

        # Set user GPU quota
        set_user_gpu_quota(username, quota)

        print(f"Successfully set GPU quota for user {username}: {quota} GPU minutes")

    except Exception as e:
        logger.error(f"User-gpu-quota command failed: {e}")
        sys.exit(1)


def set_account_gpu_quota_command(account: str, quota: int) -> None:
    """
    Execute the account-gpu-quota command to set account GPU quota.
    This command can only be executed by root user.
    """
    try:
        # Check if running as root user
        current_user = auth.get_current_user()
        if current_user != "root":
            logger.error(
                f"Account-gpu-quota command can only be executed by root user, not by "
                f"{current_user}"
            )
            sys.exit(1)

        # Initialize database only if missing
        init_database()

        # Set account GPU quota
        set_account_gpu_quota(account, quota)

        print(f"Successfully set GPU quota for account {account}: {quota} GPU minutes")

    except Exception as e:
        logger.error(f"Account-gpu-quota command failed: {e}")
        sys.exit(1)


def adjust_command(
    username: Optional[str],
    account: Optional[str],
    cpu: bool,
    gpu: bool,
    minutes: Optional[int],
    hours: Optional[int],
) -> None:
    """
    Adjust consumed CPU/GPU time for a user or account.
    This command can only be executed by root user.
    """
    try:
        current_user = auth.get_current_user()
        if current_user != "root":
            logger.error(
                "adjust command can only be executed by root user, not by %s",
                current_user,
            )
            sys.exit(1)

        target_type = "user" if username is not None else "account"
        target_name = username if username is not None else account
        if target_name is None:
            logger.error("adjust command requires --user or --account")
            sys.exit(1)

        resource = "cpu" if cpu else "gpu"
        if minutes is not None:
            delta_minutes = minutes
            unit_label = "minutes"
        else:
            if hours is None:
                logger.error("adjust command requires --minutes or --hours")
                sys.exit(1)
            delta_minutes = hours * 60
            unit_label = "hours"

        init_database()
        new_total = adjust_consumed_minutes(
            target_type, target_name, resource, delta_minutes
        )

        delta_source = minutes if minutes is not None else hours
        target_label = "user" if target_type == "user" else "account"
        print(
            (
                f"Successfully adjusted {resource.upper()} consumed time "
                f"for {target_label} {target_name}: "
                f"{delta_source:+d} {unit_label} (new total: {new_total} minutes)"
            )
        )

    except Exception as e:
        logger.error(f"adjust command failed: {e}")
        sys.exit(1)


def show_gpu_factors_command() -> None:
    """
    Execute the gpu-factors command to display current GPU charging factors.
    """
    try:
        if not os.path.exists(slurm_quota.DB_PATH):
            print("Database not found. No GPU factors configured.")
            return

        with sqlite3.connect(slurm_quota.DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT gpu_type, factor FROM gpu_factors ORDER BY gpu_type")
            rows = cursor.fetchall()

            if not rows:
                print("No GPU factors configured. Default factor: 1.0")
                return

            # Separate default factor from type-specific factors
            default_factor = None
            type_factors: List[Tuple[str, float]] = []

            for gpu_type, factor in rows:
                try:
                    factor_f = float(factor)
                    if gpu_type == "default":
                        default_factor = factor_f
                    else:
                        type_factors.append((gpu_type, factor_f))
                except (TypeError, ValueError):
                    logger.warning(f"Invalid factor for GPU type {gpu_type}: {factor}")

            # Display results
            print("GPU Charging Factors")
            print("-" * 40)

            # Show default factor
            if default_factor is not None:
                print(f"{'default':<20} {default_factor:.3f}")
            else:
                print(f"{'default':<20} 1.000 (not configured)")

            # Show type-specific factors
            if type_factors:
                for gpu_type, factor in sorted(type_factors):
                    print(f"{gpu_type:<20} {factor:.3f}")

    except sqlite3.Error as e:
        logger.error(f"Failed to query GPU factors: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"gpu-factors command failed: {e}")
        sys.exit(1)


def set_gpu_factor_command(gpu_type: str, factor: float) -> None:
    """
    Execute the set-gpu-factor command to set GPU charging factors.
    This command can only be executed by root user.
    """
    try:
        # Check if running as root user
        current_user = auth.get_current_user()
        if current_user != "root":
            logger.error(
                f"set-gpu-factor command can only be executed by root user, not by "
                f"{current_user}"
            )
            sys.exit(1)

        if factor <= 0:
            logger.error(f"Factor must be positive, got: {factor}")
            sys.exit(1)

        # Initialize database only if missing
        init_database()

        # Set GPU factor
        set_gpu_factor(gpu_type, factor)

        print(f"Successfully set GPU factor for type {gpu_type}: {factor}")

    except Exception as e:
        logger.error(f"set-gpu-factor command failed: {e}")
        sys.exit(1)


def set_default_quotas_command(
    user_cpu: Optional[int],
    user_gpu: Optional[int],
    account_cpu: Optional[int],
    account_gpu: Optional[int],
) -> None:
    """
    Set default quotas used when new users/accounts are auto-created.
    This command can only be executed by root user.
    """
    try:
        current_user = auth.get_current_user()
        if current_user != "root":
            logger.error(
                "set-default-quotas command can only be executed by root user, "
                "not by %s",
                current_user,
            )
            sys.exit(1)

        if all(
            value is None for value in (user_cpu, user_gpu, account_cpu, account_gpu)
        ):
            logger.error(
                "At least one option must be provided: --user-cpu, --user-gpu, "
                "--account-cpu, --account-gpu"
            )
            sys.exit(1)

        init_database()
        set_default_quota_settings(user_cpu, user_gpu, account_cpu, account_gpu)
        print("Successfully updated default quotas")
    except Exception as e:
        logger.error(f"set-default-quotas command failed: {e}")
        sys.exit(1)


def show_default_quotas_command() -> None:
    """
    Display current default quotas for newly auto-created users/accounts.
    """

    def fmt(m: int) -> str:
        return "∞" if m == -1 else str(m)

    try:
        init_database()
        settings = get_default_quota_settings()
        print("Default Quotas For New Entities")
        print("-" * 40)
        print(
            f"{'user_cpu_minutes':<24} "
            f"{fmt(settings['default_user_quota_cpu_minutes'])}"
        )
        print(
            f"{'user_gpu_minutes':<24} "
            f"{fmt(settings['default_user_quota_gpu_minutes'])}"
        )
        print(
            f"{'account_cpu_minutes':<24} "
            f"{fmt(settings['default_account_quota_cpu_minutes'])}"
        )
        print(
            f"{'account_gpu_minutes':<24} "
            f"{fmt(settings['default_account_quota_gpu_minutes'])}"
        )
    except Exception as e:
        logger.error(f"default-quotas command failed: {e}")
        sys.exit(1)


def prune_command(
    preallocs: bool = False,
    users: bool = False,
    accounts: bool = False,
    all_targets: bool = False,
    dry_run: bool = False,
    user_filter: Optional[str] = None,
    account_filter: Optional[str] = None,
) -> None:
    """
    Execute the prune command to remove selected orphan/unused entries.
    This command can only be executed by root user.
    """
    try:
        current_user = auth.get_current_user()
        if current_user != "root":
            logger.error(
                f"Prune command can only be executed by root user, not by {current_user}"
            )
            sys.exit(1)

        if all_targets or not any((preallocs, users, accounts)):
            targets = {"preallocs", "users", "accounts"}
        else:
            targets: set[str] = set()
            if preallocs:
                targets.add("preallocs")
            if users:
                targets.add("users")
            if accounts:
                targets.add("accounts")
        counts = prune_resources(
            targets,
            dry_run=dry_run,
            user_filter=user_filter,
            account_filter=account_filter,
        )
        selected_counts = {target: counts[target] for target in sorted(targets)}

        if dry_run:
            print(
                "Dry-run: would remove "
                f"{selected_counts.get('preallocs', 0)} orphan preallocation(s), "
                f"{selected_counts.get('users', 0)} user(s), "
                f"{selected_counts.get('accounts', 0)} account(s)"
            )
            return

        if all(count == 0 for count in selected_counts.values()):
            print("Nothing to prune")
            return

        print(
            "Removed "
            f"{selected_counts.get('preallocs', 0)} orphan preallocation(s), "
            f"{selected_counts.get('users', 0)} user(s), "
            f"{selected_counts.get('accounts', 0)} account(s)"
        )
    except Exception as e:
        logger.error(f"Prune command failed: {e}")
        sys.exit(1)


def login_command(username: Optional[str] = None, save: bool = False) -> None:
    """
    Obtain a JWT from the HTTP service.

    By default, prints the token to stdout. When save is True, persists the
    token for subsequent API calls.
    """
    selected_username = username
    if not selected_username:
        try:
            selected_username = auth.get_current_user()
        except KeyError:
            sys.exit(1)

    password = getpass.getpass(f"Password for {selected_username}: ")
    try:
        api = APIClient()
        token = api.login(selected_username, password)
        if save:
            token_path = save_service_token(token)
            print(f"Authentication token saved to {token_path}")
        else:
            print(token)
    except ServiceHTTPError as e:
        if e.status == 401:
            logger.error("Invalid user or password")
        elif e.status == 404:
            logger.error(
                "Authentication is not enabled on the slurm-quota service "
                "(POST /login returned HTTP 404)"
            )
        else:
            logger.error(f"Login failed: HTTP {e.status}")
        sys.exit(1)
    except OSError as e:
        logger.error(f"Failed to save authentication token: {e}")
        sys.exit(1)
    except URLError as e:
        logger.error(f"Failed to contact slurm-quota service: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Login failed: {e}")
        sys.exit(1)


def show_user_stats(
    username: Optional[str] = None,
    account: Optional[str] = None,
    show_all: bool = False,
    display_hours: bool = False,
) -> None:
    """
    Display resource statistics for users in table format.

    Args:
        username: The username to query (if None and not show_all, uses current user)
        account: The account to query in the accounts table.
        show_all: If True, show all users
    """
    selected_username = username
    if not show_all and not selected_username and not account:
        try:
            selected_username = auth.get_current_user()
        except KeyError:
            sys.exit(1)

    def format_value(value_minutes: int) -> str:
        if display_hours:
            return f"{value_minutes / 60:.2f}"
        return f"{value_minutes}"

    def compute_first_column_width(
        users: List[Dict[str, Any]],
        accounts: List[Dict[str, Any]],
        max_width: int = 30,
    ) -> int:
        labels = [
            "USER",
            "ACCOUNT",
            *[str(item.get("username", "?")) for item in users],
            *[str(item.get("account", "?")) for item in accounts],
        ]
        longest_label = max(len(label) for label in labels)
        return min(longest_label, max_width)

    try:
        api = APIClient(token=load_service_token())
        users_data, accounts_data = api.stats(selected_username, account, show_all)

        if not users_data and not accounts_data:
            if username:
                print(f"No data found for user: {username}")
            elif account:
                print(f"No data found for account: {account}")
            else:
                print("No users found in service")
            return

        first_column_width = compute_first_column_width(users_data, accounts_data)

        def format_first_column(value: str) -> str:
            value_str = str(value)
            if len(value_str) > first_column_width:
                if first_column_width <= 1:
                    value_str = "…" * first_column_width
                else:
                    value_str = f"{value_str[: first_column_width - 1]}…"
            return f"{value_str:<{first_column_width}}"

        if users_data:
            header = (
                f"{'':<{first_column_width}} | {'CPU':^66} | {'GPU':^66} |\n"
                f"{format_first_column('USER')} | {'CONSUMED':>11} {'PREALLOC(JOBS)':>15} {'QUOTA':>8} {'STATUS':<29} "
                f"| {'CONSUMED':>11} {'PREALLOC(JOBS)':>15} {'QUOTA':>8} {'STATUS':<29} | {'LAST UPDATED':<25}"
            )
            print(header)
            print("-" * int(len(header) / 2))

            for item in users_data:
                uname = item.get("username", "?")
                job_count = int(item.get("job_count", 0))
                last_updated = item.get("last_updated")
                last_updated_str = format_timestamp_with_timezone(last_updated)

                cpu_consumed = int(item.get("total_consumed_cpu_minutes", 0))
                cpu_preallocated = int(item.get("total_preallocated_cpu_minutes", 0))
                cpu_quota = int(item.get("quota_cpu_minutes", -1))
                cpu_total_used = cpu_consumed + cpu_preallocated
                if cpu_quota == -1:
                    cpu_quota_str = "∞"
                    cpu_status_bar = ""
                else:
                    cpu_quota_str = format_value(cpu_quota)
                    cpu_status_bar = create_status_bar(cpu_total_used, cpu_quota)
                cpu_consumed_str = format_value(cpu_consumed)
                cpu_preallocated_str = f"{format_value(cpu_preallocated)}({job_count})"

                gpu_consumed = int(item.get("total_consumed_gpu_minutes", 0))
                gpu_preallocated = int(item.get("total_preallocated_gpu_minutes", 0))
                gpu_quota = int(item.get("quota_gpu_minutes", -1))
                gpu_total_used = gpu_consumed + gpu_preallocated
                if gpu_quota == -1:
                    gpu_quota_str = "∞"
                    gpu_status_bar = ""
                else:
                    gpu_quota_str = format_value(gpu_quota)
                    gpu_status_bar = create_status_bar(gpu_total_used, gpu_quota)
                gpu_consumed_str = format_value(gpu_consumed)
                gpu_preallocated_str = f"{format_value(gpu_preallocated)}({job_count})"

                print(
                    f"{format_first_column(uname)} | {cpu_consumed_str:>11} {cpu_preallocated_str:>15} {cpu_quota_str:>8} {cpu_status_bar:<29} "
                    f"| {gpu_consumed_str:>11} {gpu_preallocated_str:>15} {gpu_quota_str:>8} {gpu_status_bar:<29} | {last_updated_str:<25}"
                )
            print()

        header = (
            f"{'':<{first_column_width}} | {'CPU':^66} | {'GPU':^66} |\n"
            f"{format_first_column('ACCOUNT')} | {'CONSUMED':>11} {'PREALLOC(JOBS)':>15} {'QUOTA':>8} {'STATUS':<29} "
            f"| {'CONSUMED':>11} {'PREALLOC(JOBS)':>15} {'QUOTA':>8} {'STATUS':<29} | {'LAST UPDATED':<25}"
        )
        print(header)
        print("-" * int(len(header) / 2))

        for item in accounts_data:
            account_name = item.get("account", "?")
            job_count = int(item.get("job_count", 0))
            last_updated = item.get("last_updated")
            last_updated_str = format_timestamp_with_timezone(last_updated)

            cpu_consumed = int(item.get("total_consumed_cpu_minutes", 0))
            cpu_preallocated = int(item.get("total_preallocated_cpu_minutes", 0))
            cpu_quota = int(item.get("quota_cpu_minutes", -1))
            cpu_total_used = cpu_consumed + cpu_preallocated
            if cpu_quota == -1:
                cpu_quota_str = "∞"
                cpu_status_bar = " " * 25
            else:
                cpu_quota_str = format_value(cpu_quota)
                cpu_status_bar = create_status_bar(cpu_total_used, cpu_quota)
            cpu_consumed_str = format_value(cpu_consumed)
            cpu_preallocated_str = f"{format_value(cpu_preallocated)}({job_count})"

            gpu_consumed = int(item.get("total_consumed_gpu_minutes", 0))
            gpu_preallocated = int(item.get("total_preallocated_gpu_minutes", 0))
            gpu_quota = int(item.get("quota_gpu_minutes", -1))
            gpu_total_used = gpu_consumed + gpu_preallocated
            if gpu_quota == -1:
                gpu_quota_str = "∞"
                gpu_status_bar = " " * 25
            else:
                gpu_quota_str = format_value(gpu_quota)
                gpu_status_bar = create_status_bar(gpu_total_used, gpu_quota)
            gpu_consumed_str = format_value(gpu_consumed)
            gpu_preallocated_str = f"{format_value(gpu_preallocated)}({job_count})"

            print(
                f"{format_first_column(account_name)} | {cpu_consumed_str:>11} {cpu_preallocated_str:>15} {cpu_quota_str:>8} {cpu_status_bar:<29} "
                f"| {gpu_consumed_str:>11} {gpu_preallocated_str:>15} {gpu_quota_str:>8} {gpu_status_bar:<29} | {last_updated_str:<25}"
            )
    except ServiceHTTPError as e:
        logger.error(f"Failed to fetch stats: HTTP {e.status}")
        sys.exit(1)
    except URLError as e:
        logger.error(f"Failed to contact slurm-quota service: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to retrieve stats from service: {e}")
        sys.exit(1)
