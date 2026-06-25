# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

import os
from pathlib import Path

from slurm_quota.web.settings import DEFAULT_ENV_FILE, load_env_file

env_file = os.environ.get("SLURM_QUOTA_WEB_ENV_FILE", DEFAULT_ENV_FILE)
load_env_file(Path(env_file))
