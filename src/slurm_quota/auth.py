# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Process identity helpers."""

import logging
import os
import pwd

logger = logging.getLogger("slurm_quota")


def get_current_user() -> str:
    """
    Get the current user name based on the process UID.

    Returns:
        The username of the current process owner
    """
    current_uid = os.getuid()
    try:
        return pwd.getpwuid(current_uid).pw_name
    except KeyError:
        logger.error(f"Unable to get user name for UID {current_uid}")
        raise


def require_slurm_user() -> None:
    """Raise PermissionError unless the current process runs as the slurm user."""
    current_user = get_current_user()
    if current_user != "slurm":
        raise PermissionError(f"Must be run as slurm user, not {current_user}")
