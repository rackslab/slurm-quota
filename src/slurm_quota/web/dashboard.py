# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Presentation helpers for the stats dashboard."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _format_minutes(value_minutes: int, display_hours: bool) -> str:
    if display_hours:
        return f"{value_minutes / 60:.2f}"
    return str(value_minutes)


def _quota_label(quota: int, display_hours: bool) -> str:
    if quota < 0:
        return "∞"
    return _format_minutes(quota, display_hours)


def _usage_percent(consumed: int, preallocated: int, quota: int) -> Optional[float]:
    if quota <= 0:
        return None
    return min(((consumed + preallocated) / quota) * 100.0, 100.0)


def _status_class(percent: Optional[float]) -> str:
    if percent is None:
        return "bar-unlimited"
    if percent >= 95.0:
        return "bar-danger"
    if percent >= 80.0:
        return "bar-warning"
    return "bar-ok"


def decorate_rows(
    rows: List[Dict[str, Any]], name_key: str, display_hours: bool
) -> List[Dict[str, Any]]:
    decorated: List[Dict[str, Any]] = []
    for item in rows:
        cpu_consumed = _parse_int(item.get("total_consumed_cpu_minutes"))
        cpu_preallocated = _parse_int(item.get("total_preallocated_cpu_minutes"))
        cpu_quota = _parse_int(item.get("quota_cpu_minutes"), -1)
        cpu_percent = _usage_percent(cpu_consumed, cpu_preallocated, cpu_quota)

        gpu_consumed = _parse_int(item.get("total_consumed_gpu_minutes"))
        gpu_preallocated = _parse_int(item.get("total_preallocated_gpu_minutes"))
        gpu_quota = _parse_int(item.get("quota_gpu_minutes"), -1)
        gpu_percent = _usage_percent(gpu_consumed, gpu_preallocated, gpu_quota)

        decorated.append(
            {
                "name": str(item.get(name_key, "?")),
                "job_count": _parse_int(item.get("job_count")),
                "last_updated": item.get("last_updated") or "n/a",
                "cpu": {
                    "consumed": _format_minutes(cpu_consumed, display_hours),
                    "preallocated": _format_minutes(cpu_preallocated, display_hours),
                    "quota": _quota_label(cpu_quota, display_hours),
                    "percent": cpu_percent,
                    "status_class": _status_class(cpu_percent),
                },
                "gpu": {
                    "consumed": _format_minutes(gpu_consumed, display_hours),
                    "preallocated": _format_minutes(gpu_preallocated, display_hours),
                    "quota": _quota_label(gpu_quota, display_hours),
                    "percent": gpu_percent,
                    "status_class": _status_class(gpu_percent),
                },
            }
        )
    return decorated
