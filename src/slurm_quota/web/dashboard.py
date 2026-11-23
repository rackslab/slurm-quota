# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Presentation helpers for the stats dashboard."""

from __future__ import annotations

from typing import Any

from slurm_quota.units import format_quota, format_seconds, usage_ratio


def _parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _usage_percent(consumed: int, preallocated: int, quota: int) -> float | None:
    ratio = usage_ratio(consumed + preallocated, quota)
    return None if ratio is None else ratio * 100.0


def _status_class(percent: float | None) -> str:
    if percent is None:
        return "bar-unlimited"
    if percent >= 95.0:
        return "bar-danger"
    if percent >= 80.0:
        return "bar-warning"
    return "bar-ok"


def decorate_breakdown(
    entries: list[dict[str, Any]], display_unit: str
) -> list[dict[str, Any]]:
    """Format consumption breakdown entries of user/account pairs for display."""
    return [
        {
            "username": str(entry.get("username", "?")),
            "account": str(entry.get("account", "?")),
            "last_updated": entry.get("last_updated") or "n/a",
            "cpu": {
                "consumed": format_seconds(
                    _parse_int(entry.get("consumed_cpu_seconds")), display_unit
                ),
            },
            "gpu": {
                "consumed": format_seconds(
                    _parse_int(entry.get("consumed_gpu_seconds")), display_unit
                ),
            },
        }
        for entry in entries
    ]


def decorate_rows(
    rows: list[dict[str, Any]],
    name_key: str,
    display_unit: str,
    breakdown: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Format user or account stats rows for display.

    Args:
        rows: User or account rows from the stats API.
        name_key: "username" or "account".
        display_unit: Unit of displayed times.
        breakdown: Decorated breakdown entries (see decorate_breakdown()) to
            attach to rows, or None. Each row then lists the entries of its user
            or account under the breakdown key, named after the other side of
            the pair.
    """
    other_key = "account" if name_key == "username" else "username"
    decorated: list[dict[str, Any]] = []
    for item in rows:
        cpu_consumed = _parse_int(item.get("total_consumed_cpu_seconds"))
        cpu_preallocated = _parse_int(item.get("total_preallocated_cpu_seconds"))
        cpu_quota = _parse_int(item.get("quota_cpu_minutes"), -1)
        cpu_percent = _usage_percent(cpu_consumed, cpu_preallocated, cpu_quota)

        gpu_consumed = _parse_int(item.get("total_consumed_gpu_seconds"))
        gpu_preallocated = _parse_int(item.get("total_preallocated_gpu_seconds"))
        gpu_quota = _parse_int(item.get("quota_gpu_minutes"), -1)
        gpu_percent = _usage_percent(gpu_consumed, gpu_preallocated, gpu_quota)

        name = str(item.get(name_key, "?"))
        decorated.append(
            {
                "name": name,
                "breakdown": [
                    dict(entry, name=entry[other_key])
                    for entry in breakdown or []
                    if entry[name_key] == name
                ],
                "comment": str(item.get("comment") or ""),
                "job_count": _parse_int(item.get("job_count")),
                "last_updated": item.get("last_updated") or "n/a",
                "cpu": {
                    "consumed": format_seconds(cpu_consumed, display_unit),
                    "preallocated": format_seconds(cpu_preallocated, display_unit),
                    "quota": format_quota(cpu_quota, display_unit),
                    "quota_minutes": cpu_quota,
                    "percent": cpu_percent,
                    "status_class": _status_class(cpu_percent),
                },
                "gpu": {
                    "consumed": format_seconds(gpu_consumed, display_unit),
                    "preallocated": format_seconds(gpu_preallocated, display_unit),
                    "quota": format_quota(gpu_quota, display_unit),
                    "quota_minutes": gpu_quota,
                    "percent": gpu_percent,
                    "status_class": _status_class(gpu_percent),
                },
            }
        )
    return decorated
