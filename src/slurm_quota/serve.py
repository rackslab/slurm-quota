# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""HTTP JSON API server for stats."""

import json

import os
import socket
import sqlite3
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import slurm_quota
from slurm_quota.database import query_accounts_aggregate, query_users_aggregate
from slurm_quota import slurm as slurm_integration

import logging

logger = logging.getLogger("slurm_quota")


class _RequestHandler(BaseHTTPRequestHandler):
    server_version = f"slurm-quota-http/{slurm_quota.APP_VERSION}"

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        logger.info("%s - - %s", self.address_string(), format % args)

    def do_GET(self) -> None:
        # Make sure self.server is InactivityHTTPServer with touch_activity method
        assert isinstance(self.server, InactivityHTTPServer)
        # Mark last activity
        self.server.touch_activity()

        if self.path.startswith("/health"):
            self._send_json({"status": "ok"})
            return

        if self.path.startswith("/stats"):
            # Parse query params.
            username_param: Optional[str] = None
            account_param: Optional[str] = None
            query_params = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            usernames = query_params.get("username", [])
            accounts = query_params.get("account", [])
            if usernames:
                username_param = usernames[0]
            if accounts:
                account_param = accounts[0]
            if username_param and account_param:
                self._send_json(
                    {
                        "error": "bad_request",
                        "message": "username and account are mutually exclusive",
                    },
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            try:
                if not os.path.exists(slurm_quota.DB_PATH):
                    self._send_json({"users": [], "accounts": []}, status=200)
                    return
                with sqlite3.connect(slurm_quota.DB_PATH) as conn:
                    users = query_users_aggregate(conn, username_param or None)
                    # When a username is provided, filter accounts to that user's associations
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
                        # Account selection is account-centric; do not include user rows.
                        users = []
                    accounts = query_accounts_aggregate(conn, accounts_filter)
                self._send_json({"users": users, "accounts": accounts}, status=200)
            except sqlite3.Error as e:
                logger.error("/stats query failed: %s", e)
                self._send_json({"error": "db_error"}, status=500)
            return

        # Default 404
        self._send_json({"error": "not_found"}, status=404)


class InactivityHTTPServer(HTTPServer):
    def __init__(self, server_address, RequestHandlerClass, idle_timeout: int = 600):
        super().__init__(server_address, RequestHandlerClass)
        # Get idle timeout from command line or environment variable. Special value 0
        # disables idle shutdown (infinite timeout).
        self._idle_timeout = max(0, int(idle_timeout))
        self._last_activity = time.monotonic()

    def touch_activity(self) -> None:
        self._last_activity = time.monotonic()

    def serve_until_idle(self) -> None:
        self.timeout = 1.0  # seconds
        while True:
            # handle_request() blocks up to self.timeout
            self.handle_request()
            # If idle timeout is disabled, continue to the next iteration.
            if self._idle_timeout == 0:
                continue
            # If idle timeout is enabled, check if the last activity was too long ago.
            if time.monotonic() - self._last_activity > self._idle_timeout:
                logger.info("Idle timeout reached; exiting")
                break


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
        httpd = InactivityHTTPServer(("0.0.0.0", 0), _RequestHandler, idle_timeout)
        httpd.socket = sd_sock
        # essential: prevent server from closing sd_sock on shutdown duplication issues
        httpd.server_bind = lambda: None  # ty: ignore[invalid-assignment]
        httpd.server_activate = lambda: None  # ty: ignore[invalid-assignment]
        try:
            httpd.serve_until_idle()
        finally:
            try:
                sd_sock.close()
            except Exception:
                pass
        return

    # Fallback: bind our own TCP socket (useful for manual runs)
    addr = (host, int(port))
    logger.info("Starting HTTP JSON service on %s:%s", host, port)
    httpd = InactivityHTTPServer(addr, _RequestHandler, idle_timeout)
    try:
        httpd.serve_until_idle()
    finally:
        try:
            httpd.server_close()
        except Exception:
            pass
