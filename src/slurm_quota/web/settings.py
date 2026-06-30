# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Environment-based configuration for the slurm-quota web dashboard."""

from __future__ import annotations

import logging
import os
import site
import sysconfig
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ENV_FILE = "/etc/default/slurm-quota-web"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5000
DEFAULT_SESSION_DAYS = 7

logger = logging.getLogger("slurm_quota")


def _parse_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _parse_int(value: str | None, *, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _optional_path(value: str | None) -> Path | None:
    if value is None:
        return None
    return Path(value)


@dataclass(frozen=True)
class SlurmQuotaWebSettings:
    """Web dashboard configuration read from the process environment."""

    quota_url: str | None
    token: str | None
    session_key: str | None
    session_key_file: Path | None
    secure_cookies: bool
    session_days: int
    assets_dir: Path | None
    host: str
    port: int
    debug: bool

    @classmethod
    def from_os_environ(cls) -> SlurmQuotaWebSettings:
        return cls(
            quota_url=os.environ.get("SLURM_QUOTA_URL"),
            token=os.environ.get("SLURM_QUOTA_TOKEN"),
            session_key=os.environ.get("SLURM_QUOTA_WEB_SESSION_KEY"),
            session_key_file=_optional_path(
                os.environ.get("SLURM_QUOTA_WEB_SESSION_KEY_FILE")
            ),
            secure_cookies=_parse_bool(
                os.environ.get("SLURM_QUOTA_WEB_SECURE_COOKIES")
            ),
            session_days=max(
                _parse_int(
                    os.environ.get("SLURM_QUOTA_WEB_SESSION_DAYS"),
                    default=DEFAULT_SESSION_DAYS,
                ),
                DEFAULT_SESSION_DAYS,
            ),
            assets_dir=_optional_path(os.environ.get("SLURM_QUOTA_WEB_ASSETS_DIR")),
            host=os.environ.get("SLURM_QUOTA_WEB_HOST", DEFAULT_HOST),
            port=_parse_int(
                os.environ.get("SLURM_QUOTA_WEB_PORT"), default=DEFAULT_PORT
            ),
            debug=_parse_bool(os.environ.get("SLURM_QUOTA_WEB_DEBUG")),
        )

    def assets_root(self) -> Path:
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
        if self.assets_dir is not None:
            return self.assets_dir

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

    def load_session_key(self) -> str | None:
        """Return the session signing key from env or a key file."""
        if self.session_key:
            return self.session_key

        if self.session_key_file is None:
            return None

        try:
            return self.session_key_file.read_text().rstrip("\n")
        except OSError as exc:
            logger.error(
                "Unable to read SLURM_QUOTA_WEB_SESSION_KEY_FILE %s: %s",
                self.session_key_file,
                exc,
            )
            return None


def load_env_file(path: Path) -> None:
    """Parse KEY=value lines from path into os.environ (setdefault per key)."""
    if not path.is_file():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)
