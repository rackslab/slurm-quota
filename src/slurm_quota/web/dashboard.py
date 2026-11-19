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


def decorate_rows(
    rows: list[dict[str, Any]], name_key: str, display_hours: bool
) -> list[dict[str, Any]]:
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

        decorated.append(
            {
                "name": str(item.get(name_key, "?")),
                "job_count": _parse_int(item.get("job_count")),
                "last_updated": item.get("last_updated") or "n/a",
                "cpu": {
                    "consumed": format_seconds(cpu_consumed, display_hours),
                    "preallocated": format_seconds(cpu_preallocated, display_hours),
                    "quota": format_quota(cpu_quota, display_hours),
                    "quota_minutes": cpu_quota,
                    "percent": cpu_percent,
                    "status_class": _status_class(cpu_percent),
                },
                "gpu": {
                    "consumed": format_seconds(gpu_consumed, display_hours),
                    "preallocated": format_seconds(gpu_preallocated, display_hours),
                    "quota": format_quota(gpu_quota, display_hours),
                    "quota_minutes": gpu_quota,
                    "percent": gpu_percent,
                    "status_class": _status_class(gpu_percent),
                },
            }
        )
    return decorated
