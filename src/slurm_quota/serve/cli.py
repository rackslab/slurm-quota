# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""CLI and server lifecycle for slurm-quota-serve."""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import socket
import ssl
import sys
import threading
import time
from pathlib import Path
from typing import Any

from werkzeug.serving import make_server

from slurm_quota.cli import add_common_arguments
from slurm_quota.log import setup_logging
from slurm_quota.serve.app import SlurmQuotaServeApp
from slurm_quota.serve.settings import (
    ServeSetupError,
    conf_defs_path,
    load_log_settings,
    site_config_path,
)

logger = logging.getLogger("slurm_quota")


def _idle_watcher(app: SlurmQuotaServeApp, server: Any, idle_timeout: int) -> None:
    while idle_timeout > 0:
        time.sleep(1.0)
        if time.monotonic() - app._last_activity > idle_timeout:
            logger.info("Idle timeout reached; exiting")
            server.shutdown()
            break


def _run_server(
    app: SlurmQuotaServeApp,
    host: str,
    port: int,
    idle_timeout: int,
    sd_sock: socket.socket | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> None:
    idle_timeout = max(0, int(idle_timeout))
    app.touch_activity()

    if sd_sock is not None:
        server = make_server(
            "0.0.0.0", 0, app, fd=sd_sock.fileno(), ssl_context=ssl_context
        )
    else:
        server = make_server(host, int(port), app, ssl_context=ssl_context)

    if idle_timeout > 0:
        threading.Thread(
            target=_idle_watcher,
            args=(app, server, idle_timeout),
            daemon=True,
        ).start()

    try:
        server.serve_forever()
    finally:
        server.server_close()


def _systemd_listen_socket() -> socket.socket | None:
    try:
        listen_pid = int(os.environ.get("LISTEN_PID", "0"))
        listen_fds = int(os.environ.get("LISTEN_FDS", "0"))
    except ValueError:
        return None
    if listen_pid != os.getpid() or listen_fds < 1:
        return None
    fd = 3  # per systemd convention
    try:
        s = socket.fromfd(fd, socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s
    except OSError:
        try:
            s = socket.fromfd(fd, socket.AF_UNIX, socket.SOCK_STREAM)
            return s
        except OSError:
            return None


def run_serve_command(
    app: SlurmQuotaServeApp,
    host: str,
    port: int,
    idle_timeout: int,
    conf_defs: Path,
    site_config: Path,
) -> None:
    try:
        app.setup(conf_defs, site_config)
    except ServeSetupError as exc:
        logger.error("%s", exc)
        sys.exit(1)

    sd_sock = _systemd_listen_socket()
    if sd_sock is not None:
        logger.info("Starting %s JSON service on systemd socket", app.scheme)
        try:
            _run_server(
                app,
                host,
                port,
                idle_timeout,
                sd_sock=sd_sock,
                ssl_context=app.ssl_context,
            )
        finally:
            with contextlib.suppress(Exception):
                sd_sock.close()
        return

    logger.info("Starting %s JSON service on %s:%s", app.scheme, host, port)
    _run_server(app, host, port, idle_timeout, ssl_context=app.ssl_context)


def main() -> None:
    """Main entry point for the slurm-quota-serve script."""
    parser = argparse.ArgumentParser(
        prog="slurm-quota-serve",
        description="Serve HTTP JSON API for Slurm quota",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    add_common_arguments(parser)
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind if not socket-activated (default: %(default)s)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9911,
        help="Port to bind if not socket-activated (default: %(default)s)",
    )
    parser.add_argument(
        "--idle-timeout",
        type=int,
        default=600,
        help=(
            "Exit after N seconds of inactivity; 0 disables idle timeout "
            "(default: %(default)s)"
        ),
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
        "--dump-config",
        action="store_true",
        help="Dump resolved configuration and exit",
    )

    args = parser.parse_args()
    config_log_flags, config_debug_flags = load_log_settings(
        args.conf_defs, args.config
    )
    setup_logging(
        args,
        config_log_flags=config_log_flags,
        config_debug_flags=config_debug_flags,
    )

    app = SlurmQuotaServeApp()
    app.register()

    if args.dump_config:
        app.load_settings(args.conf_defs, args.config)
        app.dump()
        return

    run_serve_command(
        app,
        args.host,
        args.port,
        args.idle_timeout,
        args.conf_defs,
        args.config,
    )
