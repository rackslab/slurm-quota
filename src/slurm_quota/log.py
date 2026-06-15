# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Logging configuration for slurm-quota."""

import logging
import sys

logger = logging.getLogger("slurm_quota")


def setup_logging(debug: bool = False, quiet: bool = False) -> None:
    """
    Setup logging configuration.

    Args:
        debug: If True, set logging level to DEBUG
        quiet: If True, set logging level to WARNING
    """
    if debug:
        level = logging.DEBUG
    elif quiet:
        level = logging.WARNING
    else:
        level = logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stderr),
        ],
        force=True,
    )
