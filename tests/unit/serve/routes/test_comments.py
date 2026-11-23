"""Unit tests for /comments endpoint and comments appended with reasons."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

from slurm_quota.database import grant_operator, set_account_comment
from tests.unit.serve.routes.base import ServeRoutesTestCase, app
from tests.unit.serve.support import issue_test_app_token


class TestCommentsRoute(ServeRoutesTestCase):
    def _comment(self, table: str, key: str, name: str):
        with self.db_connection() as conn:
            row = conn.execute(
                f"SELECT comment FROM {table} WHERE {key} = ?", (name,)
            ).fetchone()
        return None if row is None else row[0]

    def test_admin_sets_user_comment(self):
        client = app.test_client()
        with self.assertLogs("slurm_quota", level="INFO") as log_cm:
            resp = client.put(
                "/comments/users/bob",
                headers=self._headers("alice"),
                json={"comment": "  PhD student  "},
            )
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(
            log_cm.output,
            [
                "INFO:slurm_quota:comment user: by=alice name=bob "
                "comment='PhD student'",
            ],
        )
        self.assertEqual(self._comment("users", "username", "bob"), "PhD student")

    def test_operator_sets_account_comment(self):
        with self.db_connection() as conn:
            grant_operator(conn, "carol")
            conn.commit()
        client = app.test_client()
        resp = client.put(
            "/comments/accounts/hpc",
            headers=self._headers("carol"),
            json={"comment": "Project X"},
        )
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(self._comment("accounts", "account", "hpc"), "Project X")

    def test_null_or_empty_comment_clears_comment(self):
        set_account_comment("hpc", "Project X")
        client = app.test_client()
        for value in (None, "", "   "):
            set_account_comment("hpc", "Project X")
            with self.assertLogs("slurm_quota", level="INFO") as log_cm:
                resp = client.put(
                    "/comments/accounts/hpc",
                    headers=self._headers("alice"),
                    json={"comment": value},
                )
            self.assertEqual(resp.status_code, 204)
            self.assertEqual(
                log_cm.output,
                [
                    "INFO:slurm_quota:comment account: by=alice name=hpc comment=n/a",
                ],
            )
            self.assertIsNone(self._comment("accounts", "account", "hpc"))

    def test_user_cannot_set_comment(self):
        self._request_expecting_abort(
            "PUT",
            "/comments/users/bob",
            403,
            description="Insufficient permissions",
            headers=self._headers("bob"),
            json={"comment": "hello"},
        )

    def _app_headers(self, scopes: list[str]) -> dict[str, str]:
        token, _ = issue_test_app_token(self._site_ini, "alice", scopes)
        return {"Authorization": f"Bearer {token}"}

    def test_application_token_with_comments_scope(self):
        client = app.test_client()
        headers = self._app_headers(["comments:write"])
        resp = client.put(
            "/comments/accounts/hpc", headers=headers, json={"comment": "Project X"}
        )
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(self._comment("accounts", "account", "hpc"), "Project X")
        # Comments scope does not allow to set quotas
        self._request_expecting_abort(
            "PUT",
            "/quotas/accounts/hpc/cpu",
            403,
            description="Token scope insufficient",
            headers=headers,
            json={"quota_minutes": 500},
        )

    def test_application_token_with_quotas_scope_cannot_set_comment(self):
        self._request_expecting_abort(
            "PUT",
            "/comments/accounts/hpc",
            403,
            description="Token scope insufficient",
            headers=self._app_headers(["quotas:write"]),
            json={"comment": "Project X"},
        )

    def test_unknown_entity_returns_404(self):
        client = app.test_client()
        resp = client.put(
            "/comments/groups/hpc",
            headers=self._headers("alice"),
            json={"comment": "hello"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_invalid_name_returns_400(self):
        self._request_expecting_abort(
            "PUT",
            "/comments/accounts/bad name",
            400,
            description="Invalid account",
            headers=self._headers("alice"),
            json={"comment": "hello"},
        )

    def test_invalid_comments_return_400(self):
        cases = [
            ({}, "comment is required"),
            ({"comment": 42}, "comment must be a string or null"),
            ({"comment": "two\nlines"}, "comment must be a single line"),
            ({"comment": "x" * 1001}, "comment must be at most 1000 characters"),
        ]
        for body, description in cases:
            with self.subTest(description=description):
                self._request_expecting_abort(
                    "PUT",
                    "/comments/users/bob",
                    400,
                    description=description,
                    headers=self._headers("alice"),
                    json=body,
                )


class TestReasonAppendedToComments(ServeRoutesTestCase):
    def _comments(self) -> tuple:
        with self.db_connection() as conn:
            user = conn.execute(
                "SELECT comment FROM users WHERE username = 'bob'"
            ).fetchone()
            account = conn.execute(
                "SELECT comment FROM accounts WHERE account = 'hpc'"
            ).fetchone()
        return (user and user[0], account and account[0])

    def test_quota_reason_appended_by_default(self):
        set_account_comment("hpc", "Project X")
        client = app.test_client()
        resp = client.put(
            "/quotas/accounts/hpc/cpu",
            headers=self._headers("alice"),
            json={"quota_minutes": 500, "reason": "extension approved"},
        )
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(self._comments()[1], "Project X | extension approved")

    def test_quota_reason_not_appended_when_disabled(self):
        set_account_comment("hpc", "Project X")
        client = app.test_client()
        with self.assertLogs("slurm_quota", level="INFO") as log_cm:
            resp = client.put(
                "/quotas/accounts/hpc/cpu",
                headers=self._headers("alice"),
                json={
                    "quota_minutes": 500,
                    "reason": "extension approved",
                    "append_reason": False,
                },
            )
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(
            log_cm.output,
            [
                "INFO:slurm_quota:quota account cpu: by=alice name=hpc "
                "value=500 reason='extension approved'",
            ],
        )
        self.assertEqual(self._comments()[1], "Project X")

    def test_quota_without_reason_keeps_comment(self):
        set_account_comment("hpc", "Project X")
        client = app.test_client()
        resp = client.put(
            "/quotas/accounts/hpc/cpu",
            headers=self._headers("alice"),
            json={"quota_minutes": 500},
        )
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(self._comments()[1], "Project X")

    def test_invalid_append_reason_returns_400(self):
        self._request_expecting_abort(
            "PUT",
            "/quotas/users/bob/cpu",
            400,
            description="append_reason must be a boolean",
            headers=self._headers("alice"),
            json={"quota_minutes": 500, "append_reason": "no"},
        )

    def test_quota_set_when_append_reason_fails(self):
        client = app.test_client()
        with (
            patch(
                "slurm_quota.serve.routes.append_account_comment",
                side_effect=sqlite3.OperationalError("locked"),
            ),
            self.assertLogs("slurm_quota", level="INFO") as log_cm,
        ):
            resp = client.put(
                "/quotas/accounts/hpc/cpu",
                headers=self._headers("alice"),
                json={"quota_minutes": 500, "reason": "extension approved"},
            )
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.get_json(), {"error": "db_error"})
        self.assertIn(
            "ERROR:slurm_quota:append reason to account comment failed: locked",
            log_cm.output,
        )
        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT quota_cpu_minutes FROM accounts WHERE account = 'hpc'"
            ).fetchone()
        self.assertEqual(row, (500,))

    def test_quotas_scope_saves_reason_in_comment(self):
        # Reasons are saved in comments without comments:write scope
        token, _ = issue_test_app_token(self._site_ini, "alice", ["quotas:write"])
        client = app.test_client()
        resp = client.put(
            "/quotas/accounts/hpc/cpu",
            headers={"Authorization": f"Bearer {token}"},
            json={"quota_minutes": 500, "reason": "extension approved"},
        )
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(self._comments()[1], "extension approved")

    def test_adjust_reason_appended_to_user_and_account(self):
        with self.db_connection() as conn:
            self.insert_consumption(conn, "bob", "hpc", cpu_minutes=10)
            conn.commit()
        client = app.test_client()
        resp = client.patch(
            "/consumption/bob/hpc/cpu",
            headers=self._headers("alice"),
            json={"delta_seconds": -60, "reason": "billing error"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._comments(), ("billing error", "billing error"))

    def test_adjust_reason_not_appended_when_disabled(self):
        with self.db_connection() as conn:
            self.insert_consumption(conn, "bob", "hpc", cpu_minutes=10)
            conn.commit()
        client = app.test_client()
        resp = client.patch(
            "/consumption/bob/hpc/cpu",
            headers=self._headers("alice"),
            json={
                "delta_seconds": -60,
                "reason": "billing error",
                "append_reason": False,
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._comments(), (None, None))
