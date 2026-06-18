# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Environment-based configuration for the slurm-quota web dashboard."""

from __future__ import annotations

import logging
import os
import site
import sysconfig
from pathlib import Path
from typing import Optional

logger = logging.getLogger("slurm_quota")


def assets_root() -> Path:
    """Return the directory containing templates/ and static/ for the dashboard.

    Resolution order:
    1. SLURM_QUOTA_WEB_ASSETS_DIR when set (custom layout).
    2. Repo-root web/ when running from a git checkout (src/slurm_quota/web/).
    3. Install prefix data directory (slurm-quota/web under sysconfig data path),
       used by normal pip install into a virtualenv or system prefix.
    4. User site-packages install (~/.local/slurm-quota/web) when the package
       is loaded from a pip install --user environment.
    5. /usr/share/slurm-quota/web as the default fallback for distro packages.
    """
    env_root = os.environ.get("SLURM_QUOTA_WEB_ASSETS_DIR")
    if env_root:
        return Path(env_root)

    repo_web = Path(__file__).resolve().parents[3] / "web"
    if repo_web.is_dir():
        return repo_web

    data_root = Path(sysconfig.get_path("data")) / "slurm-quota" / "web"
    if (data_root / "templates").is_dir():
        return data_root

    user_site = site.getusersitepackages()
    if user_site and str(Path(__file__).resolve()).startswith(user_site):
        user_data = Path(site.getuserbase()) / "slurm-quota" / "web"
        if (user_data / "templates").is_dir():
            return user_data

    return Path("/usr/share/slurm-quota/web")


def load_session_key() -> Optional[str]:
    """Return the session signing key from env or a key file."""
    session_key = os.environ.get("SLURM_QUOTA_WEB_SESSION_KEY")
    if session_key:
        return session_key

    session_key_file = os.environ.get("SLURM_QUOTA_WEB_SESSION_KEY_FILE")
    if not session_key_file:
        return None

    try:
        return Path(session_key_file).read_text().rstrip("\n")
    except OSError as exc:
        logger.error(
            "Unable to read SLURM_QUOTA_WEB_SESSION_KEY_FILE %s: %s",
            session_key_file,
            exc,
        )
        return None
