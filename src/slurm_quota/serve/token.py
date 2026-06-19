# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""CLI for issuing JWT tokens (root only)."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rfl.authentication.errors import JWTPrivateKeyLoaderError
from rfl.authentication.jwt import JWTManager
from rfl.authentication.user import AuthenticatedUser

from slurm_quota import APP_VERSION, auth
from slurm_quota.log import setup_logging
from slurm_quota.serve.settings import (
    ServeSetupError,
    conf_defs_path,
    load_serve_settings,
    site_config_path,
)

logger = logging.getLogger("slurm_quota")


def issue_token(
    username: str,
    *,
    duration: int | None,
    conf_defs: Path,
    site_config: Path,
) -> str:
    settings = load_serve_settings(conf_defs, site_config)
    if settings.authentication.method != "jwt":
        raise ServeSetupError(
            "slurm-quota-token requires authentication.method=jwt in serve.ini"
        )
    try:
        jwt = JWTManager.key(
            audience=settings.jwt.audience,
            algorithm=settings.jwt.algorithm,
            path=settings.jwt.key,
            create=settings.jwt.create,
            create_parent=settings.jwt.create_parent,
        )
    except JWTPrivateKeyLoaderError as exc:
        raise ServeSetupError(str(exc)) from exc
    token_duration = duration if duration is not None else settings.jwt.duration
    user = AuthenticatedUser(login=username, groups=[])
    return jwt.generate(user, token_duration)


def main() -> None:
    """Main entry point for the slurm-quota-token script."""
    parser = argparse.ArgumentParser(
        prog="slurm-quota-token",
        description="Issue JWT tokens for slurm-quota-serve (root only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    log_level_group = parser.add_mutually_exclusive_group()
    log_level_group.add_argument(
        "--debug",
        action="store_true",
        help="Print debug output",
    )
    log_level_group.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Only print errors and warnings",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {APP_VERSION}",
        help="Show program version and exit",
    )
    parser.add_argument(
        "--conf-defs",
        type=Path,
        default=conf_defs_path(),
        help="Path to YAML settings definition file (default: %(default)s)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=site_config_path(),
        help="Path to site INI configuration file (default: %(default)s)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        metavar="DAYS",
        help="Token validity in days (default: jwt.duration from serve.ini)",
    )
    parser.add_argument(
        "username",
        help="Username to embed in the JWT",
    )

    args = parser.parse_args()
    setup_logging(debug=args.debug, quiet=args.quiet)

    try:
        current_user = auth.get_current_user()
    except KeyError:
        sys.exit(1)

    if current_user != "root":
        logger.error(
            "slurm-quota-token can only be executed by root user, not by %s",
            current_user,
        )
        sys.exit(1)

    try:
        token = issue_token(
            args.username,
            duration=args.duration,
            conf_defs=args.conf_defs,
            site_config=args.config,
        )
    except ServeSetupError as exc:
        logger.error("%s", exc)
        sys.exit(1)

    print(token)
