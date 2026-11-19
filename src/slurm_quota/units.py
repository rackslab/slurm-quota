# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Conversion of consumed and preallocated times for display.

Consumed and preallocated times are reported in seconds, quotas in minutes
(-1 for unlimited). Times are converted only for display, usage is computed in
seconds like the submission check in job_submit.lua.
"""

from __future__ import annotations


def format_seconds(seconds: int, hours: bool) -> str:
    """Format a time in seconds as whole minutes (floored) or hours."""
    if hours:
        return f"{seconds / 3600:.2f}"
    return str(seconds // 60)


def format_quota(quota_minutes: int, hours: bool) -> str:
    """Format a quota in minutes as minutes or hours, ∞ when unlimited."""
    if quota_minutes < 0:
        return "∞"
    return format_seconds(quota_minutes * 60, hours)


def usage_ratio(used_seconds: int, quota_minutes: int) -> float | None:
    """Return the used share of a quota, capped at 1.0, None when unlimited."""
    if quota_minutes <= 0:
        return None
    return min(used_seconds / (quota_minutes * 60), 1.0)
