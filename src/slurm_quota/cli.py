# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""CLI entry point for slurm-quota."""

from __future__ import annotations

import argparse
import re
import sys

from slurm_quota import APP_VERSION
from slurm_quota.commands import (
    adjust_command,
    login_command,
    role_grant_command,
    role_list_command,
    role_managers_add_command,
    role_managers_list_command,
    role_managers_remove_command,
    role_revoke_command,
    role_show_command,
    set_account_gpu_quota_command,
    set_account_quota_command,
    set_default_quotas_command,
    set_gpu_factor_command,
    set_user_gpu_quota_command,
    set_user_quota_command,
    show_default_quotas_command,
    show_gpu_factors_command,
    show_user_stats,
    token_command,
)
from slurm_quota.log import setup_logging
from slurm_quota.token import ClientToken


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    """Add global options shared by slurm-quota command-line entry points."""
    log_level_group = parser.add_mutually_exclusive_group()
    log_level_group.add_argument(
        "--debug",
        action="store_true",
        help="Print debug output",
    )
    log_level_group.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Only print errors and warnings",
    )

    parser.add_argument(
        "--log-flags",
        action="append",
        default=None,
        metavar="FLAG",
        help=(
            "Component whose logs are enabled (repeatable; special value ALL). "
            "Default: slurm_quota"
        ),
    )
    parser.add_argument(
        "--debug-flags",
        action="append",
        default=None,
        metavar="FLAG",
        help=(
            "Component whose debug logs are enabled when --debug is used "
            "(repeatable; special value ALL). Default: slurm_quota"
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {APP_VERSION}",
        help="Show program version and exit",
    )


def parse_signed_int(value: str) -> int:
    """
    Parse an explicitly signed integer for CLI arguments.

    Args:
        value: Raw argument value

    Returns:
        Parsed integer value

    Raises:
        argparse.ArgumentTypeError: If value does not include an explicit sign
    """
    if not re.fullmatch(r"[+-]\d+", value):
        raise argparse.ArgumentTypeError(
            "must be an explicitly signed integer (for example: +30 or -30)"
        )
    return int(value)


def main():
    """
    Main entry point for the slurm-quota script.
    """
    parser = argparse.ArgumentParser(
        prog="slurm-quota",
        description="Slurm Quota Management Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    add_common_arguments(parser)

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Login command
    login_parser = subparsers.add_parser(
        "login",
        help=("Authenticate with LDAP on REST API service and obtain a JWT token"),
    )
    login_parser.add_argument(
        "username",
        nargs="?",
        help="LDAP username (default: current OS user)",
    )
    login_parser.add_argument(
        "--save",
        action="store_true",
        help=f"Save token to {ClientToken.path()}",
    )

    # Token command
    token_parser = subparsers.add_parser(
        "token",
        help="Show current API token information",
    )
    token_parser.add_argument(
        "--save",
        action="store_true",
        help=f"Save SLURM_QUOTA_TOKEN to {ClientToken.path()}",
    )

    # Stats command
    stats_parser = subparsers.add_parser(
        "stats", help="Show resource statistics for users"
    )
    stats_parser.add_argument(
        "username",
        nargs="?",
        help="Username to query (optional, shows all users if not specified)",
    )
    stats_parser.add_argument(
        "--user",
        dest="user",
        help="Username to query (same behavior as positional username)",
    )
    stats_parser.add_argument(
        "--account",
        dest="account",
        help="Account to query",
    )
    stats_parser.add_argument(
        "--all",
        action="store_true",
        help="Show all users (same as not specifying username)",
    )
    stats_parser.add_argument(
        "--hours",
        action="store_true",
        help="Display stats values in hours instead of minutes",
    )

    # Role commands
    role_parser = subparsers.add_parser(
        "role",
        help="Show or manage REST API user roles",
    )
    role_subparsers = role_parser.add_subparsers(dest="role_command")

    role_subparsers.add_parser("show", help="Show current user role")

    role_subparsers.add_parser(
        "list",
        help="List all users with roles (admin only)",
    )

    role_grant_parser = role_subparsers.add_parser(
        "grant",
        help="Grant operator or manager role to a user (admin only)",
    )
    role_grant_parser.add_argument(
        "role",
        choices=("operator", "manager"),
        help="Role to grant",
    )
    role_grant_parser.add_argument("username", help="Username to grant role to")

    role_revoke_parser = role_subparsers.add_parser(
        "revoke",
        help="Revoke operator or manager role from a user (admin only)",
    )
    role_revoke_parser.add_argument(
        "role",
        choices=("operator", "manager"),
        help="Role to revoke",
    )
    role_revoke_parser.add_argument("username", help="Username to revoke role from")

    role_managers_parser = role_subparsers.add_parser(
        "managers",
        help="Manage manager account assignments (admin only)",
    )
    role_managers_subparsers = role_managers_parser.add_subparsers(
        dest="managers_command",
        required=True,
    )
    role_managers_list_parser = role_managers_subparsers.add_parser(
        "list",
        help="List accounts assigned to a manager",
    )
    role_managers_list_parser.add_argument("username", help="Manager username")

    role_managers_add_parser = role_managers_subparsers.add_parser(
        "add",
        help="Assign an account to a manager",
    )
    role_managers_add_parser.add_argument("username", help="Manager username")
    role_managers_add_parser.add_argument("account", help="Account to assign")

    role_managers_remove_parser = role_managers_subparsers.add_parser(
        "remove",
        help="Remove an account from a manager",
    )
    role_managers_remove_parser.add_argument("username", help="Manager username")
    role_managers_remove_parser.add_argument("account", help="Account to remove")

    # Adjust command
    adjust_parser = subparsers.add_parser(
        "adjust",
        help="Adjust consumed CPU/GPU time for a user/account (root only)",
    )
    adjust_target_group = adjust_parser.add_mutually_exclusive_group(required=True)
    adjust_target_group.add_argument("--user", help="Username to adjust")
    adjust_target_group.add_argument("--account", help="Account to adjust")
    adjust_resource_group = adjust_parser.add_mutually_exclusive_group(required=True)
    adjust_resource_group.add_argument(
        "--cpu", action="store_true", help="Adjust consumed CPU minutes"
    )
    adjust_resource_group.add_argument(
        "--gpu", action="store_true", help="Adjust consumed GPU minutes"
    )
    adjust_delta_group = adjust_parser.add_mutually_exclusive_group(required=True)
    adjust_delta_group.add_argument(
        "--minutes",
        type=parse_signed_int,
        help="Signed delta in minutes (must include + or - sign)",
    )
    adjust_delta_group.add_argument(
        "--hours",
        type=parse_signed_int,
        help="Signed delta in hours (must include + or - sign)",
    )
    adjust_parser.add_argument(
        "--reason",
        help="Optional reason for the adjustment (recorded in audit logs)",
    )

    # User-quota command
    user_quota_parser = subparsers.add_parser(
        "user-quota",
        help="Set quota for a user (operator or admin)",
    )
    user_quota_parser.add_argument("username", help="Username to set quota for")
    user_quota_parser.add_argument(
        "quota", type=int, help="Quota in CPU minutes (-1 for unlimited)"
    )
    user_quota_parser.add_argument(
        "--reason",
        help="Optional reason for the change (recorded in audit logs)",
    )

    # Account-quota command
    account_quota_parser = subparsers.add_parser(
        "account-quota",
        help="Set quota for an account (operator or admin)",
    )
    account_quota_parser.add_argument("account", help="Account to set quota for")
    account_quota_parser.add_argument(
        "quota", type=int, help="Quota in CPU minutes (-1 for unlimited)"
    )
    account_quota_parser.add_argument(
        "--reason",
        help="Optional reason for the change (recorded in audit logs)",
    )

    # User-gpu-quota command
    user_gpu_quota_parser = subparsers.add_parser(
        "user-gpu-quota",
        help="Set GPU quota for a user (operator or admin)",
    )
    user_gpu_quota_parser.add_argument("username", help="Username to set GPU quota for")
    user_gpu_quota_parser.add_argument(
        "quota", type=int, help="Quota in GPU minutes (-1 for unlimited)"
    )
    user_gpu_quota_parser.add_argument(
        "--reason",
        help="Optional reason for the change (recorded in audit logs)",
    )

    # Account-gpu-quota command
    account_gpu_quota_parser = subparsers.add_parser(
        "account-gpu-quota",
        help="Set GPU quota for an account (operator or admin)",
    )
    account_gpu_quota_parser.add_argument(
        "account", help="Account to set GPU quota for"
    )
    account_gpu_quota_parser.add_argument(
        "quota", type=int, help="Quota in GPU minutes (-1 for unlimited)"
    )
    account_gpu_quota_parser.add_argument(
        "--reason",
        help="Optional reason for the change (recorded in audit logs)",
    )

    # GPU factors command (show)
    subparsers.add_parser("gpu-factors", help="Show current GPU charging factors")

    # GPU factor command (set)
    gpu_factor_parser = subparsers.add_parser(
        "set-gpu-factor",
        help="Set GPU charging factor for a GPU type (operator or admin role required)",
    )
    gpu_factor_parser.add_argument(
        "gpu_type",
        help="GPU type to set factor for (use 'default' for the default factor)",
    )
    gpu_factor_parser.add_argument(
        "factor",
        type=float,
        help="Charging factor (positive float, e.g. 0.5)",
    )

    # Default quotas commands (show)
    subparsers.add_parser(
        "default-quotas",
        help="Show default quotas used for new users/accounts (operator or admin)",
    )

    # Default quotas commands (set)
    set_default_quotas_parser = subparsers.add_parser(
        "set-default-quotas",
        help="Set default quotas used for new users/accounts (operator or admin)",
    )
    set_default_quotas_parser.add_argument(
        "--user-cpu",
        type=int,
        help="Default CPU quota for new users in minutes (-1 for unlimited)",
    )
    set_default_quotas_parser.add_argument(
        "--user-gpu",
        type=int,
        help="Default GPU quota for new users in minutes (-1 for unlimited)",
    )
    set_default_quotas_parser.add_argument(
        "--account-cpu",
        type=int,
        help="Default CPU quota for new accounts in minutes (-1 for unlimited)",
    )
    set_default_quotas_parser.add_argument(
        "--account-gpu",
        type=int,
        help="Default GPU quota for new accounts in minutes (-1 for unlimited)",
    )

    args = parser.parse_args()

    setup_logging(args)

    if args.command == "login":
        login_command(args.username, args.save)
    elif args.command == "token":
        token_command(save=args.save)
    elif args.command == "stats":
        if args.username and args.user:
            parser.error("stats: positional username and --user are mutually exclusive")
        selected_username = args.user or args.username
        if selected_username and args.account:
            parser.error("stats: user selection and --account are mutually exclusive")
        show_user_stats(selected_username, args.account, args.all, args.hours)
    elif args.command == "role":
        if args.role_command == "show":
            role_show_command()
        elif args.role_command == "list":
            role_list_command()
        elif args.role_command == "grant":
            role_grant_command(args.role, args.username)
        elif args.role_command == "revoke":
            role_revoke_command(args.role, args.username)
        elif args.role_command == "managers":
            if args.managers_command == "list":
                role_managers_list_command(args.username)
            elif args.managers_command == "add":
                role_managers_add_command(args.username, args.account)
            elif args.managers_command == "remove":
                role_managers_remove_command(args.username, args.account)
        else:
            role_parser.print_help()
            sys.exit(1)
    elif args.command == "adjust":
        adjust_command(
            args.user,
            args.account,
            args.cpu,
            args.gpu,
            args.minutes,
            args.hours,
            args.reason,
        )
    elif args.command == "user-quota":
        set_user_quota_command(args.username, args.quota, args.reason)
    elif args.command == "account-quota":
        set_account_quota_command(args.account, args.quota, args.reason)
    elif args.command == "user-gpu-quota":
        set_user_gpu_quota_command(args.username, args.quota, args.reason)
    elif args.command == "account-gpu-quota":
        set_account_gpu_quota_command(args.account, args.quota, args.reason)
    elif args.command == "gpu-factors":
        show_gpu_factors_command()
    elif args.command == "set-gpu-factor":
        set_gpu_factor_command(args.gpu_type, args.factor)
    elif args.command == "default-quotas":
        show_default_quotas_command()
    elif args.command == "set-default-quotas":
        set_default_quotas_command(
            args.user_cpu, args.user_gpu, args.account_cpu, args.account_gpu
        )
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
