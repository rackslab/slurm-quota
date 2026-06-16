# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Charge command for Slurm job completion."""

import argparse
import logging
import sys

from slurm_quota import APP_VERSION, auth
from slurm_quota.log import setup_logging
from slurm_quota.database import init_database, update_user_and_account_resources
from slurm_quota.slurm import get_job_info_from_environment

logger = logging.getLogger("slurm_quota")


def charge_command() -> None:
    """
    Execute the charge command to update user resource consumption.
    This command can only be executed by the slurm system user.
    """
    try:
        current_user = auth.get_current_user()
        if current_user != "slurm":
            logger.error(
                f"Charge command can only be executed by slurm user, not by "
                f"{current_user}"
            )
            sys.exit(1)

        init_database()

        (
            username,
            job_id,
            consumed_cpu_minutes,
            account,
            job_uuid,
            consumed_gpu_minutes,
        ) = get_job_info_from_environment()

        prealloc_status = update_user_and_account_resources(
            username, account, consumed_cpu_minutes, job_uuid, consumed_gpu_minutes
        )

        logger.info(
            (
                f"charge: user={username} account={account} job_id={job_id} "
                f"+{consumed_cpu_minutes} CPUmins +{consumed_gpu_minutes} GPUmins "
                f"uuid={job_uuid or 'none'} prealloc_status={prealloc_status}"
            )
        )

    except Exception as e:
        logger.error(f"Charge command failed: {e}")
        sys.exit(1)


def main() -> None:
    """Main entry point for the slurm-quota-charge script."""
    parser = argparse.ArgumentParser(
        prog="slurm-quota-charge",
        description="Update user resource consumption on Slurm job completion",
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

    args = parser.parse_args()
    setup_logging(debug=args.debug, quiet=args.quiet)
    charge_command()
