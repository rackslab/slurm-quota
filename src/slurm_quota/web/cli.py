# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""CLI entry point for the slurm-quota-web standalone server."""

from __future__ import annotations

from slurm_quota.web.app import SlurmQuotaWebApp
from slurm_quota.web.settings import SlurmQuotaWebSettings


def main() -> None:
    settings = SlurmQuotaWebSettings.from_os_environ()
    app = SlurmQuotaWebApp(settings)
    app.register()
    app.run(
        host=settings.host,
        port=settings.port,
        debug=settings.debug,
    )
