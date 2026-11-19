# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Synchronization of Slurm associations in the slurm-quota database."""

import argparse
import logging
import sqlite3
import sys

from slurm_quota import auth, slurm as slurm_integration
from slurm_quota.cli import add_common_arguments
from slurm_quota.database import init_database, sync_associations
from slurm_quota.log import setup_logging

logger = logging.getLogger("slurm_quota")


def sync_command() -> None:
    """
    Execute the sync command to cache Slurm associations in the database.
    This command can only be executed by the slurm system user.

    The cache is left untouched when Slurm accounting cannot be queried, or
    when it reports no user association. A cluster without any user
    association cannot run jobs, so this result is considered an error: once
    applied, job_submit.lua would refuse all jobs.
    """
    try:
        auth.require_slurm_user()
    except PermissionError:
        logger.error(
            "Sync command can only be executed by slurm user, not by "
            f"{auth.get_current_user()}"
        )
        sys.exit(1)

    try:
        init_database()
        associations = slurm_integration.get_associations()
        if not associations:
            logger.error(
                "Sync command failed: no user association reported by sacctmgr, "
                "keeping cached associations"
            )
            sys.exit(1)
        result = sync_associations(associations)
    except (slurm_integration.SlurmQueryError, sqlite3.Error, OSError) as err:
        logger.error(f"Sync command failed: {err}")
        sys.exit(1)

    for username, account in result.added:
        logger.info("Added Slurm association %s/%s", username, account)
    for username, account in result.removed:
        logger.info("Removed Slurm association %s/%s", username, account)
    logger.info(
        "Synchronized %d Slurm associations (%d added, %d removed)",
        len(associations),
        len(result.added),
        len(result.removed),
    )


def main() -> None:
    """Main entry point for the slurm-quota-sync script."""
    parser = argparse.ArgumentParser(
        prog="slurm-quota-sync",
        description="Synchronize Slurm associations in the slurm-quota database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    add_common_arguments(parser)

    args = parser.parse_args()
    setup_logging(args)
    sync_command()
