# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Flask WSGI dashboard for slurm-quota stats."""

from slurm_quota.web.app import SlurmQuotaWebApp

application = SlurmQuotaWebApp()
application.register()

__all__ = ["application"]
