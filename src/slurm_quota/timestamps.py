# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Conversion of timestamps for display."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def format_timestamp_with_timezone(timestamp_str: str | None) -> str:
    """
    Format a timestamp string to display with local timezone.

    Args:
        timestamp_str: UTC timestamp string from REST API in ISO format

    Returns:
        Formatted timestamp string with local timezone, or "N/A" if invalid
    """
    if not timestamp_str:
        return "N/A"

    try:
        # ISO format from API without timezone - assume UTC
        dt = datetime.fromisoformat(timestamp_str)
        # Replace timezone info to treat as UTC
        dt = dt.replace(tzinfo=timezone.utc)

        # Convert to local timezone
        local_dt = dt.astimezone()
        # Format as YYYY-MM-DD HH:MM:SS TZ
        return local_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    except (ValueError, TypeError) as e:
        logger.debug(f"Failed to parse timestamp '{timestamp_str}': {e}")
        return "N/A"
