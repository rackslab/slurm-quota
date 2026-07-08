# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Logging configuration for slurm-quota."""

import logging

from rfl.log import setup_logger

logger = logging.getLogger("slurm_quota")


def setup_logging(debug: bool = False, quiet: bool = False) -> None:
    """
    Setup logging configuration.

    Args:
        debug: If True, set logging level to DEBUG
        quiet: If True, set logging level to WARNING
    """
    setup_logger(
        debug=debug,
        level="WARNING" if quiet else None,
        log_flags=["slurm_quota"],
        debug_flags=["slurm_quota"] if debug else [],
    )
