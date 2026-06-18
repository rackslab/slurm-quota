# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""CLI entry point for the slurm-quota-web standalone server."""

from __future__ import annotations

import os
from typing import Any

from slurm_quota.web.app import SlurmQuotaWebApp


def _parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def main() -> None:
    app = SlurmQuotaWebApp()
    app.register()
    host = os.environ.get("SLURM_QUOTA_WEB_HOST", "127.0.0.1")
    port = _parse_int(os.environ.get("SLURM_QUOTA_WEB_PORT"), 5000)
    debug = os.environ.get("SLURM_QUOTA_WEB_DEBUG") == "1"
    app.run(host=host, port=port, debug=debug)
