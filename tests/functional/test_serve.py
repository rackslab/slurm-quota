"""Functional tests: `serve` subcommand (`--host`, `--port`, `--idle-timeout`)."""

from __future__ import annotations

import http.client
import json
import os
import socket
import threading
import time
from unittest.mock import patch

from tests.functional.functional_base import FunctionalCLIBase


class TestServeCommand(FunctionalCLIBase):
    def _free_tcp_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])

    def _start_serve_thread(
        self, host: str, port: int, idle_timeout: int = 1
    ) -> threading.Thread:
        thread = threading.Thread(
            target=self.run_main,
            args=(
                [
                    "slurm-quota",
                    "serve",
                    "--host",
                    host,
                    "--port",
                    str(port),
                    "--idle-timeout",
                    str(idle_timeout),
                ],
            ),
            daemon=True,
        )
        thread.start()
        return thread

    def _wait_until_ready(self, host: str, port: int) -> None:
        deadline = time.monotonic() + 2.0
        while True:
            conn = None
            try:
                conn = http.client.HTTPConnection(host, port, timeout=1)
                conn.request("GET", "/health")
                resp = conn.getresponse()
                self.assertEqual(resp.status, 200)
                self.assertEqual(
                    json.loads(resp.read().decode("utf-8")),
                    {"status": "ok"},
                )
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
            finally:
                try:
                    if conn is not None:
                        conn.close()
                except Exception:
                    pass

    def _join_after_idle(self, thread: threading.Thread) -> None:
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())

    def test_serve_get_health_returns_ok(self):
        self.init_db()
        host = "127.0.0.1"
        port = self._free_tcp_port()
        thread = self._start_serve_thread(host, port)
        self._wait_until_ready(host, port)

        conn = http.client.HTTPConnection(host, port, timeout=2)
        try:
            conn.request("GET", "/health")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            self.assertEqual(
                json.loads(resp.read().decode("utf-8")),
                {"status": "ok"},
            )
        finally:
            conn.close()

        self._join_after_idle(thread)

    def test_serve_get_stats_returns_users_and_accounts(self):
        self.init_db()
        host = "127.0.0.1"
        port = self._free_tcp_port()
        thread = self._start_serve_thread(host, port)
        self._wait_until_ready(host, port)

        conn = http.client.HTTPConnection(host, port, timeout=2)
        try:
            conn.request("GET", "/stats")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            body = json.loads(resp.read().decode("utf-8"))
            self.assertIn("users", body)
            self.assertIn("accounts", body)
        finally:
            conn.close()

        self._join_after_idle(thread)

    def test_serve_get_stats_supports_username_query_filter(self):
        self.init_db()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("alice", 120),
            )
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("bob", 30),
            )
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("hpc", 100),
            )
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("dev", 40),
            )
            conn.commit()

        host = "127.0.0.1"
        port = self._free_tcp_port()
        with patch.object(self.sq, "get_user_accounts", return_value={"hpc"}):
            thread = self._start_serve_thread(host, port)
            self._wait_until_ready(host, port)

            conn = http.client.HTTPConnection(host, port, timeout=2)
            try:
                conn.request("GET", "/stats?username=alice")
                resp = conn.getresponse()
                self.assertEqual(resp.status, 200)
                body = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(len(body["users"]), 1)
                self.assertEqual(body["users"][0]["username"], "alice")
                self.assertEqual(len(body["accounts"]), 1)
                self.assertEqual(body["accounts"][0]["account"], "hpc")
            finally:
                conn.close()

            self._join_after_idle(thread)

    def test_serve_get_stats_supports_account_query_filter(self):
        self.init_db()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("alice", 120),
            )
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("hpc", 100),
            )
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("dev", 40),
            )
            conn.commit()

        host = "127.0.0.1"
        port = self._free_tcp_port()
        thread = self._start_serve_thread(host, port)
        self._wait_until_ready(host, port)

        conn = http.client.HTTPConnection(host, port, timeout=2)
        try:
            conn.request("GET", "/stats?account=hpc")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            body = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(body["users"], [])
            self.assertEqual(len(body["accounts"]), 1)
            self.assertEqual(body["accounts"][0]["account"], "hpc")
        finally:
            conn.close()

        self._join_after_idle(thread)

    def test_serve_get_stats_rejects_username_and_account_filters(self):
        self.init_db()
        host = "127.0.0.1"
        port = self._free_tcp_port()
        thread = self._start_serve_thread(host, port)
        self._wait_until_ready(host, port)

        conn = http.client.HTTPConnection(host, port, timeout=2)
        try:
            conn.request("GET", "/stats?username=alice&account=hpc")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 400)
            self.assertEqual(
                json.loads(resp.read().decode("utf-8")),
                {
                    "error": "bad_request",
                    "message": "username and account are mutually exclusive",
                },
            )
        finally:
            conn.close()

        self._join_after_idle(thread)

    def test_serve_get_unknown_path_returns_not_found(self):
        self.init_db()
        host = "127.0.0.1"
        port = self._free_tcp_port()
        thread = self._start_serve_thread(host, port)
        self._wait_until_ready(host, port)

        conn = http.client.HTTPConnection(host, port, timeout=2)
        try:
            conn.request("GET", "/does-not-exist")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 404)
            self.assertEqual(
                json.loads(resp.read().decode("utf-8")),
                {"error": "not_found"},
            )
        finally:
            conn.close()

        self._join_after_idle(thread)

    def test_serve_uses_systemd_socket_activation_env(self):
        self.init_db()
        host = "127.0.0.1"
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, 0))
        listener.listen(16)
        port = int(listener.getsockname()[1])

        fd3_backup = None
        had_fd3 = True
        try:
            try:
                os.fstat(3)
            except OSError:
                had_fd3 = False
            if had_fd3:
                fd3_backup = os.dup(3)

            os.dup2(listener.fileno(), 3)
            self.env({"LISTEN_PID": str(os.getpid()), "LISTEN_FDS": "1"})

            thread = self._start_serve_thread("0.0.0.0", 1, idle_timeout=1)
            self._wait_until_ready(host, port)

            conn = http.client.HTTPConnection(host, port, timeout=2)
            try:
                conn.request("GET", "/health")
                resp = conn.getresponse()
                self.assertEqual(resp.status, 200)
                self.assertEqual(
                    json.loads(resp.read().decode("utf-8")),
                    {"status": "ok"},
                )
            finally:
                conn.close()

            self._join_after_idle(thread)
        finally:
            listener.close()
            if fd3_backup is not None:
                os.dup2(fd3_backup, 3)
                os.close(fd3_backup)
            elif not had_fd3:
                try:
                    os.close(3)
                except OSError:
                    pass

    def test_serve_idle_timeout_zero_disables_shutdown(self):
        self.init_db()
        host = "127.0.0.1"
        port = self._free_tcp_port()
        thread = threading.Thread(
            target=self.run_main,
            args=(
                [
                    "slurm-quota",
                    "serve",
                    "--host",
                    host,
                    "--port",
                    str(port),
                    "--idle-timeout",
                    "0",
                ],
            ),
            daemon=True,
        )
        thread.start()

        self._wait_until_ready(host, port)
        time.sleep(1.2)
        self.assertTrue(thread.is_alive())
