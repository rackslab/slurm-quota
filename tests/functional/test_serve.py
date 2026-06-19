"""Functional tests: `slurm-quota-serve` command (`--host`, `--port`, `--idle-timeout`)."""

from __future__ import annotations

from slurm_quota.database import init_database
from slurm_quota.serve.settings import conf_defs_path

import http.client
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

from rfl.authentication.user import AuthenticatedUser

from tests.functional.functional_base import FunctionalCLIBase
from tests.unit.serve.support import (
    issue_test_token,
    write_jwt_site_ini,
    write_ldap_site_ini,
)


class TestServeCommand(FunctionalCLIBase):
    def setUp(self):
        super().setUp()
        self._patch_slurm = patch("slurm_quota.serve.app.auth.require_slurm_user")
        self._patch_slurm.start()
        self.addCleanup(self._patch_slurm.stop)
        self._config_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._config_tmp.cleanup)
        self._site_ini = write_jwt_site_ini(Path(self._config_tmp.name))
        self._token = issue_test_token(self._site_ini)

    def _free_tcp_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])

    def _start_serve_thread(
        self,
        host: str,
        port: int,
        idle_timeout: int = 1,
        extra_args: list[str] | None = None,
    ) -> threading.Thread:
        argv = [
            "slurm-quota-serve",
            "--host",
            host,
            "--port",
            str(port),
            "--idle-timeout",
            str(idle_timeout),
            "--conf-defs",
            str(conf_defs_path()),
            "--config",
            str(self._site_ini),
        ]
        if extra_args:
            argv.extend(extra_args)
        thread = threading.Thread(
            target=self.run_serve_main,
            args=(argv,),
            daemon=True,
        )
        thread.start()
        return thread

    def _write_ldap_site_ini(self, directory: Path) -> Path:
        return write_ldap_site_ini(directory)

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

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
        init_database()
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
        init_database()
        host = "127.0.0.1"
        port = self._free_tcp_port()
        thread = self._start_serve_thread(host, port)
        self._wait_until_ready(host, port)

        conn = http.client.HTTPConnection(host, port, timeout=2)
        try:
            conn.request("GET", "/stats", headers=self._auth_headers())
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            body = json.loads(resp.read().decode("utf-8"))
            self.assertIn("users", body)
            self.assertIn("accounts", body)
        finally:
            conn.close()

        self._join_after_idle(thread)

    def test_serve_get_stats_supports_username_query_filter(self):
        init_database()
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
        with patch("slurm_quota.slurm.get_user_accounts", return_value={"hpc"}):
            thread = self._start_serve_thread(host, port)
            self._wait_until_ready(host, port)

            conn = http.client.HTTPConnection(host, port, timeout=2)
            try:
                conn.request(
                    "GET", "/stats?username=alice", headers=self._auth_headers()
                )
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
        init_database()
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
            conn.request("GET", "/stats?account=hpc", headers=self._auth_headers())
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
        init_database()
        host = "127.0.0.1"
        port = self._free_tcp_port()
        thread = self._start_serve_thread(host, port)
        self._wait_until_ready(host, port)

        conn = http.client.HTTPConnection(host, port, timeout=2)
        try:
            conn.request(
                "GET",
                "/stats?username=alice&account=hpc",
                headers=self._auth_headers(),
            )
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
        init_database()
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
        init_database()
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
        init_database()
        host = "127.0.0.1"
        port = self._free_tcp_port()
        thread = threading.Thread(
            target=self.run_serve_main,
            args=(
                [
                    "slurm-quota-serve",
                    "--host",
                    host,
                    "--port",
                    str(port),
                    "--idle-timeout",
                    "0",
                    "--conf-defs",
                    str(conf_defs_path()),
                    "--config",
                    str(self._site_ini),
                ],
            ),
            daemon=True,
        )
        thread.start()

        self._wait_until_ready(host, port)
        time.sleep(1.2)
        self.assertTrue(thread.is_alive())

    def test_serve_auth_login_returns_token(self):
        init_database()
        host = "127.0.0.1"
        port = self._free_tcp_port()
        with tempfile.TemporaryDirectory() as tmpdir:
            site_ini = self._write_ldap_site_ini(Path(tmpdir))
            with patch("slurm_quota.serve.app.LDAPAuthentifier") as m_ldap_cls:
                m_ldap_cls.return_value.login.return_value = AuthenticatedUser(
                    login="alice", groups=["users"]
                )
                thread = self._start_serve_thread(
                    host,
                    port,
                    extra_args=["--config", str(site_ini)],
                )
                self._wait_until_ready(host, port)

                conn = http.client.HTTPConnection(host, port, timeout=2)
                try:
                    body = json.dumps(
                        {"username": "alice", "password": "secret"}
                    ).encode("utf-8")
                    conn.request(
                        "POST",
                        "/login",
                        body=body,
                        headers={"Content-Type": "application/json"},
                    )
                    login_resp = conn.getresponse()
                    self.assertEqual(login_resp.status, 200)
                    login_body = json.loads(login_resp.read().decode("utf-8"))
                    self.assertIn("token", login_body)
                finally:
                    conn.close()

                self._join_after_idle(thread)

    def test_serve_auth_login_rejects_bad_credentials(self):
        init_database()
        host = "127.0.0.1"
        port = self._free_tcp_port()
        with tempfile.TemporaryDirectory() as tmpdir:
            site_ini = self._write_ldap_site_ini(Path(tmpdir))
            from rfl.authentication.errors import LDAPAuthenticationError

            with patch("slurm_quota.serve.app.LDAPAuthentifier") as m_ldap_cls:
                m_ldap_cls.return_value.login.side_effect = LDAPAuthenticationError(
                    "Invalid user or password"
                )
                thread = self._start_serve_thread(
                    host,
                    port,
                    extra_args=["--config", str(site_ini)],
                )
                self._wait_until_ready(host, port)

                conn = http.client.HTTPConnection(host, port, timeout=2)
                try:
                    body = json.dumps(
                        {"username": "alice", "password": "wrong"}
                    ).encode("utf-8")
                    conn.request(
                        "POST",
                        "/login",
                        body=body,
                        headers={"Content-Type": "application/json"},
                    )
                    resp = conn.getresponse()
                    self.assertEqual(resp.status, 401)
                finally:
                    conn.close()

                self._join_after_idle(thread)

    def test_serve_auth_stats_requires_token(self):
        init_database()
        host = "127.0.0.1"
        port = self._free_tcp_port()
        with tempfile.TemporaryDirectory() as tmpdir:
            site_ini = self._write_ldap_site_ini(Path(tmpdir))
            with patch("slurm_quota.serve.app.LDAPAuthentifier") as m_ldap_cls:
                m_ldap_cls.return_value.login.return_value = AuthenticatedUser(
                    login="alice", groups=["users"]
                )
                thread = self._start_serve_thread(
                    host,
                    port,
                    extra_args=["--config", str(site_ini)],
                )
                self._wait_until_ready(host, port)

                conn = http.client.HTTPConnection(host, port, timeout=2)
                try:
                    conn.request("GET", "/stats")
                    stats_resp = conn.getresponse()
                    self.assertEqual(stats_resp.status, 403)
                    stats_body = json.loads(stats_resp.read().decode("utf-8"))
                    self.assertEqual(stats_body["error"], "forbidden")

                    login_body = json.dumps(
                        {"username": "alice", "password": "secret"}
                    ).encode("utf-8")
                    conn.request(
                        "POST",
                        "/login",
                        body=login_body,
                        headers={"Content-Type": "application/json"},
                    )
                    login_resp = conn.getresponse()
                    self.assertEqual(login_resp.status, 200)
                    token = json.loads(login_resp.read().decode("utf-8"))["token"]

                    conn.request(
                        "GET",
                        "/stats",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    authed_resp = conn.getresponse()
                    self.assertEqual(authed_resp.status, 200)
                    authed_body = json.loads(authed_resp.read().decode("utf-8"))
                    self.assertIn("users", authed_body)
                    self.assertIn("accounts", authed_body)
                finally:
                    conn.close()

                self._join_after_idle(thread)
