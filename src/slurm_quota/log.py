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
    root = logging.getLogger()
    # RFL setup_logger only adds a handler; clear first so repeated calls
    # (e.g. in tests) do not duplicate log lines, like basicConfig(force=True).
    root.handlers.clear()

    setup_logger(
        debug=debug,
        log_flags=["slurm_quota"],
        debug_flags=["slurm_quota"] if debug else [],
    )

    if quiet:
        root.setLevel(logging.WARNING)
        for handler in root.handlers:
            handler.setLevel(logging.WARNING)
