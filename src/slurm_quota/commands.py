# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""CLI command implementations."""

import getpass
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from slurm_quota import auth
from slurm_quota.client import APIClient, ServiceHTTPError, ServiceUnreachableError
from slurm_quota.database import (
    prune_resources,
)
from slurm_quota.token import ClientToken

logger = logging.getLogger("slurm_quota")

RELOGIN_GUIDANCE = "Run 'slurm-quota login --save' to obtain a new token."


def _api_client_from_token() -> APIClient:
    token = ClientToken.load_value()
    if token is None:
        logger.error("No API token available.")
        logger.info(RELOGIN_GUIDANCE)
        sys.exit(1)
    return APIClient(token=token)


def _handle_api_error(exc: ServiceHTTPError, *, forbidden_message: str) -> None:
    if exc.status == 403:
        logger.error(forbidden_message)
    elif exc.status == 401:
        if exc.message == "Token is expired":
            logger.error("Authentication token has expired.")
        else:
            detail = exc.message or "invalid or missing token"
            logger.error(f"Authentication failed: {detail}.")
        logger.info(RELOGIN_GUIDANCE)
    else:
        logger.error(f"API request failed: HTTP {exc.status}")
    sys.exit(1)


def _handle_unreachable_error(exc: ServiceUnreachableError) -> None:
    logger.error(f"Failed to contact slurm-quota service: {exc}")
    sys.exit(1)


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
    """Set user CPU quota via the REST API (operator or admin role required)."""
    try:
        _api_client_from_token().set_user_cpu_quota(username, quota)
        print(f"Successfully set quota for user {username}: {quota} CPU minutes")
    except ServiceHTTPError as exc:
        _handle_api_error(
            exc,
            forbidden_message=(
                "Access denied: operator or admin role required to set quotas"
            ),
        )
    except ServiceUnreachableError as exc:
        _handle_unreachable_error(exc)


def set_account_quota_command(account: str, quota: int) -> None:
    """Set account CPU quota via the REST API (operator or admin role required)."""
    try:
        _api_client_from_token().set_account_cpu_quota(account, quota)
        print(f"Successfully set quota for account {account}: {quota} CPU minutes")
    except ServiceHTTPError as exc:
        _handle_api_error(
            exc,
            forbidden_message=(
                "Access denied: operator or admin role required to set quotas"
            ),
        )
    except ServiceUnreachableError as exc:
        _handle_unreachable_error(exc)


def set_user_gpu_quota_command(username: str, quota: int) -> None:
    """Set user GPU quota via the REST API (operator or admin role required)."""
    try:
        _api_client_from_token().set_user_gpu_quota(username, quota)
        print(f"Successfully set GPU quota for user {username}: {quota} GPU minutes")
    except ServiceHTTPError as exc:
        _handle_api_error(
            exc,
            forbidden_message=(
                "Access denied: operator or admin role required to set quotas"
            ),
        )
    except ServiceUnreachableError as exc:
        _handle_unreachable_error(exc)


def set_account_gpu_quota_command(account: str, quota: int) -> None:
    """Set account GPU quota via the REST API (operator or admin role required)."""
    try:
        _api_client_from_token().set_account_gpu_quota(account, quota)
        print(f"Successfully set GPU quota for account {account}: {quota} GPU minutes")
    except ServiceHTTPError as exc:
        _handle_api_error(
            exc,
            forbidden_message=(
                "Access denied: operator or admin role required to set quotas"
            ),
        )
    except ServiceUnreachableError as exc:
        _handle_unreachable_error(exc)


def adjust_command(
    username: Optional[str],
    account: Optional[str],
    cpu: bool,
    gpu: bool,
    minutes: Optional[int],
    hours: Optional[int],
) -> None:
    """Adjust consumed CPU/GPU time via the REST API.

    Operator or admin role required.
    """
    try:
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

        api = _api_client_from_token()
        new_total = api.adjust_consumption(
            target_type, target_name, resource, delta_minutes
        )

        delta_source = minutes if minutes is not None else hours
        target_label = "user" if target_type == "user" else "account"
        print(
            f"Successfully adjusted {resource.upper()} consumed time "
            f"for {target_label} {target_name}: "
            f"{delta_source:+d} {unit_label} (new total: {new_total} minutes)"
        )

    except ServiceHTTPError as exc:
        _handle_api_error(
            exc,
            forbidden_message=(
                "Access denied: operator or admin role required to adjust consumption"
            ),
        )
    except ServiceUnreachableError as exc:
        _handle_unreachable_error(exc)
    except Exception as e:
        logger.error(f"adjust command failed: {e}")
        sys.exit(1)


def show_gpu_factors_command() -> None:
    """
    Execute the gpu-factors command to display current GPU charging factors.
    """
    try:
        data = _api_client_from_token().get_gpu_factors()
        default_factor = data["default_factor"]
        type_factors = sorted(data["factors"].items())

        print("GPU Charging Factors")
        print("-" * 40)
        print(f"{'default':<20} {default_factor:.3f}")
        for gpu_type, factor in type_factors:
            print(f"{gpu_type:<20} {factor:.3f}")
    except ServiceHTTPError as exc:
        _handle_api_error(
            exc,
            forbidden_message=(
                "Access denied: operator or admin role required to view GPU factors"
            ),
        )
    except ServiceUnreachableError as exc:
        _handle_unreachable_error(exc)
    except Exception as e:
        logger.error(f"gpu-factors command failed: {e}")
        sys.exit(1)


def set_gpu_factor_command(gpu_type: str, factor: float) -> None:
    """
    Execute the set-gpu-factor command to set GPU charging factors.
    Manager or admin API role required.
    """
    try:
        if factor <= 0:
            logger.error(f"Factor must be positive, got: {factor}")
            sys.exit(1)

        _api_client_from_token().set_gpu_factor(gpu_type, factor)
        print(f"Successfully set GPU factor for type {gpu_type}: {factor}")
    except ServiceHTTPError as exc:
        _handle_api_error(
            exc,
            forbidden_message=(
                "Access denied: operator or admin role required to set GPU factors"
            ),
        )
    except ServiceUnreachableError as exc:
        _handle_unreachable_error(exc)
    except Exception as e:
        logger.error(f"set-gpu-factor command failed: {e}")
        sys.exit(1)


def set_default_quotas_command(
    user_cpu: Optional[int],
    user_gpu: Optional[int],
    account_cpu: Optional[int],
    account_gpu: Optional[int],
) -> None:
    """Set default quotas via the REST API (operator or admin role required)."""
    try:
        if all(
            value is None for value in (user_cpu, user_gpu, account_cpu, account_gpu)
        ):
            logger.error(
                "At least one option must be provided: --user-cpu, --user-gpu, "
                "--account-cpu, --account-gpu"
            )
            sys.exit(1)

        _api_client_from_token().set_default_quotas(
            user_cpu=user_cpu,
            user_gpu=user_gpu,
            account_cpu=account_cpu,
            account_gpu=account_gpu,
        )
        print("Successfully updated default quotas")
    except ServiceHTTPError as exc:
        _handle_api_error(
            exc,
            forbidden_message=(
                "Access denied: operator or admin role required to set default quotas"
            ),
        )
    except ServiceUnreachableError as exc:
        _handle_unreachable_error(exc)
    except Exception as e:
        logger.error(f"set-default-quotas command failed: {e}")
        sys.exit(1)


def show_default_quotas_command() -> None:
    """Display default quotas via the REST API (operator or admin role required)."""

    def fmt(m: int) -> str:
        return "∞" if m == -1 else str(m)

    try:
        settings = _api_client_from_token().get_default_quotas()
        print("Default Quotas For New Entities")
        print("-" * 40)
        print(f"{'user_cpu_minutes':<24} {fmt(settings['user_cpu_minutes'])}")
        print(f"{'user_gpu_minutes':<24} {fmt(settings['user_gpu_minutes'])}")
        print(f"{'account_cpu_minutes':<24} {fmt(settings['account_cpu_minutes'])}")
        print(f"{'account_gpu_minutes':<24} {fmt(settings['account_gpu_minutes'])}")
    except ServiceHTTPError as exc:
        _handle_api_error(
            exc,
            forbidden_message=(
                "Access denied: operator or admin role required to view default quotas"
            ),
        )
    except ServiceUnreachableError as exc:
        _handle_unreachable_error(exc)
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
                "Prune command can only be executed by root user, "
                f"not by {current_user}"
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
        payload = api.login(selected_username, password)
        token = payload["token"]
        if save:
            token_path = ClientToken(token, "").save()
            print(f"Authentication token saved to {token_path}")
        else:
            print(token)
    except ServiceHTTPError as e:
        if e.status == 401:
            logger.error("Invalid user or password")
        elif e.status == 404:
            logger.error(
                "LDAP authentication is not enabled on the slurm-quota service "
                "(POST /login returned HTTP 404)"
            )
        else:
            logger.error(f"Login failed: HTTP {e.status}")
        sys.exit(1)
    except OSError as e:
        logger.error(f"Failed to save authentication token: {e}")
        sys.exit(1)
    except ServiceUnreachableError as exc:
        _handle_unreachable_error(exc)
    except Exception as e:
        logger.error(f"Login failed: {e}")
        sys.exit(1)


def token_command(*, save: bool = False) -> None:
    """Show current token metadata or persist SLURM_QUOTA_TOKEN to the config file."""
    if save:
        try:
            token_path = ClientToken.load(env_only=True).save()
            print(f"Authentication token saved to {token_path}")
        except ValueError:
            logger.error("SLURM_QUOTA_TOKEN is not set or is empty")
            sys.exit(1)
        except OSError as e:
            logger.error(f"Failed to save authentication token: {e}")
            sys.exit(1)
        return

    client_token = ClientToken.load()
    if client_token is None:
        logger.error("No API token available.")
        logger.info(RELOGIN_GUIDANCE)
        sys.exit(1)

    try:
        payload = client_token.decode()
        print(f"Source: {client_token.source}")
        print(f"Username: {payload.username()}")
        print(f"Expires: {payload.expiry()}")
    except ValueError as exc:
        message = str(exc)
        if message in ("invalid JWT format", "invalid JWT payload"):
            logger.error("Invalid authentication token format")
        elif message == "missing username claim":
            logger.error("Invalid authentication token: missing username claim")
        elif message == "missing expiration claim":
            logger.error("Invalid authentication token: missing expiration claim")
        else:
            logger.error("Invalid authentication token format")
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
        users: list[dict[str, Any]],
        accounts: list[dict[str, Any]],
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
        api = _api_client_from_token()
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

        def format_stats_subheader(label: str) -> str:
            cpu = f"{'CONSUMED':>11} {'PREALLOC(JOBS)':>15} {'QUOTA':>8} {'STATUS':<29}"
            gpu = (
                f"{'CONSUMED':>11} {'PREALLOC(JOBS)':>15} "
                f"{'QUOTA':>8} {'STATUS':<29} | {'LAST UPDATED':<25}"
            )
            return f"{format_first_column(label)} | {cpu} | {gpu}"

        def format_stats_row(
            label: str,
            cpu_consumed_str: str,
            cpu_preallocated_str: str,
            cpu_quota_str: str,
            cpu_status_bar: str,
            gpu_consumed_str: str,
            gpu_preallocated_str: str,
            gpu_quota_str: str,
            gpu_status_bar: str,
            last_updated_str: str,
        ) -> str:
            cpu = (
                f"{cpu_consumed_str:>11} {cpu_preallocated_str:>15} "
                f"{cpu_quota_str:>8} {cpu_status_bar:<29}"
            )
            gpu = (
                f"{gpu_consumed_str:>11} {gpu_preallocated_str:>15} "
                f"{gpu_quota_str:>8} {gpu_status_bar:<29} | {last_updated_str:<25}"
            )
            return f"{format_first_column(label)} | {cpu} | {gpu}"

        if users_data:
            header = (
                f"{'':<{first_column_width}} | {'CPU':^66} | {'GPU':^66} |\n"
                f"{format_stats_subheader('USER')}"
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
                    format_stats_row(
                        uname,
                        cpu_consumed_str,
                        cpu_preallocated_str,
                        cpu_quota_str,
                        cpu_status_bar,
                        gpu_consumed_str,
                        gpu_preallocated_str,
                        gpu_quota_str,
                        gpu_status_bar,
                        last_updated_str,
                    )
                )
            print()

        header = (
            f"{'':<{first_column_width}} | {'CPU':^66} | {'GPU':^66} |\n"
            f"{format_stats_subheader('ACCOUNT')}"
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
                format_stats_row(
                    account_name,
                    cpu_consumed_str,
                    cpu_preallocated_str,
                    cpu_quota_str,
                    cpu_status_bar,
                    gpu_consumed_str,
                    gpu_preallocated_str,
                    gpu_quota_str,
                    gpu_status_bar,
                    last_updated_str,
                )
            )
    except ServiceHTTPError as e:
        _handle_api_error(
            e,
            forbidden_message="Access denied: insufficient permissions to view stats",
        )
    except ServiceUnreachableError as exc:
        _handle_unreachable_error(exc)
    except Exception as e:
        logger.error(f"Failed to retrieve stats from service: {e}")
        sys.exit(1)


def role_show_command() -> None:
    try:
        payload = _api_client_from_token().me()
    except ServiceHTTPError as exc:
        _handle_api_error(exc, forbidden_message="Access denied")
    except ServiceUnreachableError as exc:
        _handle_unreachable_error(exc)

    username = payload.get("username", "?")
    role = payload.get("role", "?")
    print(f"Username: {username}")
    print(f"Role: {role}")


def role_list_command() -> None:
    try:
        users = _api_client_from_token().users_roles()
    except ServiceHTTPError as exc:
        _handle_api_error(
            exc, forbidden_message="Access denied: admin role required to list roles"
        )
    except ServiceUnreachableError as exc:
        _handle_unreachable_error(exc)

    if not users:
        print("No users found")
        return

    username_width = max(
        len("USERNAME"), max(len(str(u.get("username", ""))) for u in users)
    )
    role_width = max(len("ROLE"), max(len(str(u.get("role", ""))) for u in users))
    print(f"{'USERNAME':<{username_width}}  {'ROLE':<{role_width}}")
    for entry in users:
        username = str(entry.get("username", "?"))
        role = str(entry.get("role", "?"))
        print(f"{username:<{username_width}}  {role:<{role_width}}")


def role_grant_command(role: Literal["operator", "manager"], username: str) -> None:
    client = _api_client_from_token()
    try:
        client.grant_role(role, username)
    except ServiceHTTPError as exc:
        _handle_api_error(
            exc,
            forbidden_message=f"Access denied: admin role required to grant {role}",
        )
    except ServiceUnreachableError as exc:
        _handle_unreachable_error(exc)
    print(f"Granted {role} role to {username}")


def role_revoke_command(role: Literal["operator", "manager"], username: str) -> None:
    client = _api_client_from_token()
    try:
        client.revoke_role(role, username)
    except ServiceHTTPError as exc:
        _handle_api_error(
            exc,
            forbidden_message=f"Access denied: admin role required to revoke {role}",
        )
    except ServiceUnreachableError as exc:
        _handle_unreachable_error(exc)
    print(f"Revoked {role} role from {username}")


def role_managers_list_command(username: str) -> None:
    try:
        accounts = _api_client_from_token().list_manager_accounts(username)
    except ServiceHTTPError as exc:
        _handle_api_error(
            exc,
            forbidden_message=(
                "Access denied: admin role required to list manager accounts"
            ),
        )
    except ServiceUnreachableError as exc:
        _handle_unreachable_error(exc)

    if not accounts:
        print(f"No accounts assigned to manager {username}")
        return

    for account in accounts:
        print(account)


def role_managers_add_command(username: str, account: str) -> None:
    try:
        _api_client_from_token().add_manager_account(username, account)
    except ServiceHTTPError as exc:
        _handle_api_error(
            exc,
            forbidden_message=(
                "Access denied: admin role required to assign manager accounts"
            ),
        )
    except ServiceUnreachableError as exc:
        _handle_unreachable_error(exc)
    print(f"Assigned account {account} to manager {username}")


def role_managers_remove_command(username: str, account: str) -> None:
    try:
        _api_client_from_token().remove_manager_account(username, account)
    except ServiceHTTPError as exc:
        _handle_api_error(
            exc,
            forbidden_message=(
                "Access denied: admin role required to remove manager accounts"
            ),
        )
    except ServiceUnreachableError as exc:
        _handle_unreachable_error(exc)
    print(f"Removed account {account} from manager {username}")
