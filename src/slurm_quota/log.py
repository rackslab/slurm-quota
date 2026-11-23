# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Logging configuration for slurm-quota."""

from __future__ import annotations

import logging
from typing import Any

from rfl.log import setup_logger

logger = logging.getLogger("slurm_quota")

DEFAULT_LOG_FLAGS = ["slurm_quota"]
DEFAULT_DEBUG_FLAGS = ["slurm_quota"]


def setup_logging(
    args: Any,
    *,
    config_log_flags: list[str] | None = None,
    config_debug_flags: list[str] | None = None,
    config_debug: bool = False,
) -> None:
    """
    Setup logging configuration from CLI arguments and optional config defaults.

    Precedence for log and debug flags: CLI arguments, then config, then defaults.
    Debug mode is enabled by --debug or by config_debug, unless --quiet is set.

    Args:
        args: Parsed CLI namespace with debug, quiet, log_flags, and debug_flags
        config_log_flags: log_flags from configuration when not set on the CLI
        config_debug_flags: debug_flags from configuration when not set on the CLI
        config_debug: debug mode from configuration
    """
    debug = args.debug or (config_debug and not args.quiet)
    log_flags = args.log_flags if args.log_flags is not None else config_log_flags
    debug_flags = (
        args.debug_flags if args.debug_flags is not None else config_debug_flags
    )

    setup_logger(
        debug=debug,
        level="WARNING" if args.quiet else None,
        log_flags=log_flags if log_flags is not None else DEFAULT_LOG_FLAGS,
        debug_flags=(
            debug_flags
            if debug_flags is not None
            else (DEFAULT_DEBUG_FLAGS if debug else [])
        ),
    )
