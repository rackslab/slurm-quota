# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""CLI entry point for slurm-quota-prune."""

import argparse

from slurm_quota import APP_VERSION
from slurm_quota.commands import prune_command
from slurm_quota.log import setup_logging


def main() -> None:
    """Main entry point for the slurm-quota-prune script."""
    parser = argparse.ArgumentParser(
        prog="slurm-quota-prune",
        description="Prune orphaned/unused records from the slurm-quota database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

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
        "--version",
        action="version",
        version=f"%(prog)s {APP_VERSION}",
        help="Show program version and exit",
    )

    prune_target_group = parser.add_mutually_exclusive_group()
    prune_target_group.add_argument(
        "--preallocs",
        action="store_true",
        help="Prune orphan preallocations not tied to any active job",
    )
    prune_target_group.add_argument(
        "--users",
        action="store_true",
        help="Prune users with no resource consumption",
    )
    prune_target_group.add_argument(
        "--accounts",
        action="store_true",
        help="Prune accounts with no resource consumption",
    )
    prune_target_group.add_argument(
        "--all",
        action="store_true",
        help="Prune preallocations, users and accounts (default if no selector)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show how many entries would be removed without deleting anything",
    )
    parser.add_argument(
        "--user",
        help="Limit users pruning to a specific username",
    )
    parser.add_argument(
        "--account",
        help="Limit accounts pruning to a specific account",
    )

    args = parser.parse_args()
    setup_logging(debug=args.debug, quiet=args.quiet)
    prune_command(
        preallocs=args.preallocs,
        users=args.users,
        accounts=args.accounts,
        all_targets=args.all,
        dry_run=args.dry_run,
        user_filter=args.user,
        account_filter=args.account,
    )
