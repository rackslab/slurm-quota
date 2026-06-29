# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Slurm integration via subprocess and environment."""

import logging
import os
import re
import subprocess
from typing import Optional

from slurm_quota import database as database_layer

logger = logging.getLogger("slurm_quota")


def parse_alloc_tres(alloc_tres: str) -> dict[str, int]:
    """
    Parse AllocTRES field from sacct to extract GPU allocations.

    Args:
        alloc_tres: AllocTRES string, e.g.,
            "billing=1,cpu=1,gres/gpu:h100=2,gres/gpu:h200=1,mem=512M,node=1"

    Returns:
        Dictionary mapping GPU type to count (e.g., {"h100": 2, "h200": 1})
    """
    gpu_counts: dict[str, int] = {}

    if not alloc_tres:
        return gpu_counts

    # Split by comma and look for gres/gpu:TYPE=N patterns
    for item in alloc_tres.split(","):
        item = item.strip()
        # Match pattern: gres/gpu:TYPE=N or gres/gpu:TYPE=N
        match = re.match(r"gres/gpu:([^=]+)=(\d+)", item)
        if match:
            gpu_type = match.group(1).strip()
            count = int(match.group(2))
            gpu_counts[gpu_type] = gpu_counts.get(gpu_type, 0) + count

    return gpu_counts


def calculate_consumed_gpu_minutes(
    gpu_counts: dict[str, int], job_duration_minutes: int, factors: dict[str, float]
) -> int:
    """
    Calculate consumed GPU minutes based on GPU allocations, duration, and factors.

    Args:
        gpu_counts: Dictionary mapping GPU type to count
        job_duration_minutes: Job duration in minutes
        factors: Dictionary mapping GPU type to factor (from load_gpu_factors)

    Returns:
        Total GPU minutes consumed (integer)
    """
    default_factor = factors.get("__default__", 1.0)
    total_gpu_minutes = 0.0

    for gpu_type, count in gpu_counts.items():
        factor = factors.get(gpu_type, default_factor)
        total_gpu_minutes += count * job_duration_minutes * factor

    return int(total_gpu_minutes)


def get_job_info_from_sacct(job_id: str) -> tuple[Optional[str], Optional[str]]:
    """
    Get the admin_comment and AllocTRES fields for a Slurm job.

    Slurm does not provide admin_comment in jobcomp script environment variables,
    so sacct is used instead.

    Args:
        job_id: The Slurm job ID

    Returns:
        Tuple of (admin_comment, alloc_tres) or (None, None) if not found/error
    """
    try:
        result = subprocess.run(
            [
                "sacct",
                "--noheader",
                "--allocations",
                "--job",
                job_id,
                "--parsable2",
                "--format",
                "admincomment,alloctres",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        output = result.stdout.strip()
        if not output:
            return None, None

        # Parsable2 format uses | as delimiter
        parts = output.split("|", 1)
        admin_comment = parts[0].strip() if len(parts) > 0 else ""
        alloc_tres = parts[1].strip() if len(parts) > 1 else ""

        # Handle (null) case
        if admin_comment == "(null)" or not admin_comment:
            admin_comment = None
        if alloc_tres == "(null)" or not alloc_tres:
            alloc_tres = None

        return admin_comment, alloc_tres
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning(f"Failed to get job info from sacct for job {job_id}: {e}")
        return None, None


def get_account_users(account: str) -> set[str]:
    """
    Return the set of Slurm users belonging to the given account using sacctmgr.

    Args:
        account: The Slurm account name

    Returns:
        A set of usernames (may be empty when none or on error)
    """
    try:
        result = subprocess.run(
            [
                "sacctmgr",
                "list",
                "associations",
                "where",
                f"account={account}",
                "format=user",
                "-P",
                "-n",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        users = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        logger.debug(f"Account {account} has users: {users}")
        return users
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning(f"Failed to get users for account {account} via sacctmgr: {e}")
        return set()


def get_user_accounts(username: str) -> set[str]:
    """
    Return the set of Slurm accounts the given user belongs to using sacctmgr.

    Args:
        username: The Slurm username

    Returns:
        A set of account names (may be empty when none or on error)
    """
    try:
        result = subprocess.run(
            [
                "sacctmgr",
                "list",
                "associations",
                "where",
                f"user={username}",
                "format=account",
                "-P",
                "-n",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        accounts = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        logger.debug(f"User {username} belongs to accounts: {accounts}")
        return accounts
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning(f"Failed to get accounts for user {username} via sacctmgr: {e}")
        return set()


def collect_active_job_uuids() -> set[str]:
    """
    Collect active Slurm job UUIDs from squeue admin_comment field.

    Returns:
        A set of admin_comment values (UUIDs) for all jobs visible in the queue.
    """
    uuids: set[str] = set()
    try:
        result = subprocess.run(
            ["squeue", "--noheader", "--Format", "jobid,admin_comment:50"],
            capture_output=True,
            text=True,
            check=True,
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            # squeeze whitespaces to get a single space between jobid and comment
            line = re.sub(r"\s+", " ", line)
            if not line:
                continue
            # Split on first whitespace to separate jobid and comment
            parts = line.split(None, 1)
            if len(parts) == 2:
                _jobid, comment = parts
                comment = comment.strip()
                if comment:
                    uuids.add(comment)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning(f"Failed to collect active job UUIDs via squeue: {e}")
    return uuids


def get_job_info_from_environment() -> tuple[str, str, int, str, Optional[str], int]:
    """
    Extract job information from Slurm environment variables.

    Returns:
        Tuple of (username, job_id, consumed_cpu_minutes, account, job_uuid,
        consumed_gpu_minutes)

    Raises:
        ValueError: If the job ID or username is not found
        ValueError: If the start or end time is not found
    """
    try:
        # Get job ID
        job_id = os.environ.get("JOBID", "unknown")

        # Check job is member of an array. If yes, compute the job ID as
        # ARRAYJOBID_ARRAYTASKID.
        array_job_id = int(os.environ.get("ARRAYJOBID", "0"))
        array_task_id = int(os.environ.get("ARRAYTASKID", "0"))
        if array_job_id != 0:
            job_id = f"{array_job_id}_{array_task_id}"

        # Get username from Slurm environment
        username = os.environ.get("USERNAME")
        if not username:
            raise ValueError(
                "USERNAME environment variable not found for job %s", job_id
            )

        # Get account from Slurm environment
        account = os.environ.get("ACCOUNT")
        if not account:
            raise ValueError(
                "ACCOUNT environment variable not found for job %s", job_id
            )

        # Calculate consumed CPU minutes
        # PROCS * (END - START) / 60
        job_procs = int(os.environ.get("PROCS", 1))
        job_start_time = int(os.environ.get("START", 0))
        job_end_time = int(os.environ.get("END", 0))
        # If START or END is not found, consider the job ran for 0 minutes
        if not job_start_time or not job_end_time:
            job_duration_minutes = 0
        else:
            job_duration_minutes = (job_end_time - job_start_time) // 60

        consumed_cpu_minutes = job_procs * job_duration_minutes

        # Get job UUID and AllocTRES from sacct
        job_uuid, alloc_tres = get_job_info_from_sacct(job_id)
        if not job_uuid:
            logger.warning(f"No job UUID found in admin_comment for job {job_id}")

        # Calculate consumed GPU minutes
        consumed_gpu_minutes = 0
        if alloc_tres:
            gpu_factors = database_layer.load_gpu_factors()
            gpu_counts = parse_alloc_tres(alloc_tres)
            if gpu_counts:
                consumed_gpu_minutes = calculate_consumed_gpu_minutes(
                    gpu_counts, job_duration_minutes, gpu_factors
                )

        return (
            username,
            job_id,
            consumed_cpu_minutes,
            account,
            job_uuid,
            consumed_gpu_minutes,
        )

    except (ValueError, KeyError) as e:
        logger.error(f"Failed to extract job information: {e}")
        raise
