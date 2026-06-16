# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""HTTP JSON API server for stats."""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sqlite3
import sys
import threading
import time
from typing import Any, Dict, Optional, Tuple

try:
    from flask import Flask, jsonify, request
    from werkzeug.serving import make_server
except ImportError as exc:
    raise ImportError(
        "slurm-quota-serve requires Flask. Install with: pip install 'slurm-quota[serve]'"
    ) from exc

import slurm_quota
from slurm_quota import APP_VERSION
from slurm_quota.database import query_accounts_aggregate, query_users_aggregate
from slurm_quota.log import setup_logging
from slurm_quota import slurm as slurm_integration

logger = logging.getLogger("slurm_quota")

app = Flask(__name__)
_last_activity = time.monotonic()


def _touch_activity() -> None:
    global _last_activity
    _last_activity = time.monotonic()


@app.before_request
def _record_activity() -> None:
    _touch_activity()


def _fetch_stats(
    username_param: Optional[str], account_param: Optional[str]
) -> Tuple[Optional[Dict[str, Any]], Optional[Tuple[Any, int]]]:
    """Return (payload, None) on success or (None, (response, status)) on error."""
    if username_param and account_param:
        return None, (
            jsonify(
                {
                    "error": "bad_request",
                    "message": "username and account are mutually exclusive",
                }
            ),
            400,
        )

    try:
        if not os.path.exists(slurm_quota.DB_PATH):
            return {"users": [], "accounts": []}, None
        with sqlite3.connect(slurm_quota.DB_PATH) as conn:
            users = query_users_aggregate(conn, username_param or None)
            accounts_filter: Optional[set[str]] = None
            if username_param:
                try:
                    accounts_filter = slurm_integration.get_user_accounts(
                        username_param
                    )
                except Exception:
                    accounts_filter = set()
            if account_param:
                accounts_filter = {account_param}
                users = []
            accounts = query_accounts_aggregate(conn, accounts_filter)
        return {"users": users, "accounts": accounts}, None
    except sqlite3.Error as exc:
        logger.error("/stats query failed: %s", exc)
        return None, (jsonify({"error": "db_error"}), 500)


@app.get("/health")
def health() -> Any:
    return jsonify({"status": "ok"})


@app.get("/stats")
def stats() -> Any:
    username_param = (request.args.get("username") or "").strip() or None
    account_param = (request.args.get("account") or "").strip() or None
    payload, error = _fetch_stats(username_param, account_param)
    if error is not None:
        response, status = error
        return response, status
    return jsonify(payload)


@app.errorhandler(404)
def not_found(_exc: Any) -> Any:
    return jsonify({"error": "not_found"}), 404


def _idle_watcher(server: Any, idle_timeout: int) -> None:
    while idle_timeout > 0:
        time.sleep(1.0)
        if time.monotonic() - _last_activity > idle_timeout:
            logger.info("Idle timeout reached; exiting")
            server.shutdown()
            break


def _run_server(
    host: str, port: int, idle_timeout: int, sd_sock: Optional[socket.socket] = None
) -> None:
    idle_timeout = max(0, int(idle_timeout))
    _touch_activity()

    if sd_sock is not None:
        server = make_server("0.0.0.0", 0, app, fd=sd_sock.fileno())
    else:
        server = make_server(host, int(port), app)

    if idle_timeout > 0:
        threading.Thread(
            target=_idle_watcher,
            args=(server, idle_timeout),
            daemon=True,
        ).start()

    try:
        server.serve_forever()
    finally:
        server.server_close()


def _systemd_listen_socket() -> Optional[socket.socket]:
    try:
        listen_pid = int(os.environ.get("LISTEN_PID", "0"))
        listen_fds = int(os.environ.get("LISTEN_FDS", "0"))
    except ValueError:
        return None
    if listen_pid != os.getpid() or listen_fds < 1:
        return None
    fd = 3  # per systemd convention
    try:
        # Try AF_INET first; if fails, fallback to generic
        s = socket.fromfd(fd, socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s
    except OSError:
        try:
            s = socket.fromfd(fd, socket.AF_UNIX, socket.SOCK_STREAM)
            return s
        except OSError:
            return None


def run_serve_command(host: str, port: int, idle_timeout: int) -> None:
    # Check if database file exists or exit with error
    if not os.path.exists(slurm_quota.DB_PATH):
        logger.error("Database file not found: %s", slurm_quota.DB_PATH)
        sys.exit(1)

    # Prefer systemd-provided socket
    sd_sock = _systemd_listen_socket()
    if sd_sock is not None:
        logger.info("Starting HTTP JSON service on systemd socket")
        try:
            _run_server(host, port, idle_timeout, sd_sock=sd_sock)
        finally:
            try:
                sd_sock.close()
            except Exception:
                pass
        return

    # Fallback to own socket
    logger.info("Starting HTTP JSON service on %s:%s", host, port)
    _run_server(host, port, idle_timeout)


def main() -> None:
    """Main entry point for the slurm-quota-serve script."""
    parser = argparse.ArgumentParser(
        prog="slurm-quota-serve",
        description="Serve HTTP JSON API for Slurm quota",
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
        "--host",
        default="127.0.0.1",
        help="Host to bind if not socket-activated (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9911,
        help="Port to bind if not socket-activated (default: 9911)",
    )
    parser.add_argument(
        "--idle-timeout",
        type=int,
        default=600,
        help="Exit after N seconds of inactivity (0 disables idle timeout; default: 600)",
    )

    args = parser.parse_args()
    setup_logging(debug=args.debug, quiet=args.quiet)
    run_serve_command(args.host, args.port, args.idle_timeout)
