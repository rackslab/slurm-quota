"""Unit tests for slurm_quota.web.routes."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from slurm_quota.client import ServiceHTTPError, ServiceUnreachableError
from tests.test_support import SlurmQuotaTestCase
from tests.unit.web.support import (
    auth_disabled,
    auth_enabled,
    configure_web_app,
    extract_csrf,
    redirect_path,
    roles_users,
    stats_rows,
    stats_rows_with_breakdown,
    web_app,
)


class TestDashboardRoutes(SlurmQuotaTestCase):
    def setUp(self):
        super().setUp()
        configure_web_app()

    def test_dashboard_renders_user_and_account_rows(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("alice", body)
        self.assertIn("hpc", body)

    def test_forwards_username_filter_to_stats_api(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            resp = client.get("/?username=alice")
        self.assertEqual(resp.status_code, 200)
        m_client.return_value.stats.assert_called_once_with(
            "alice", None, show_all=False, include_breakdown=True
        )

    def test_forwards_account_filter_to_stats_api(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            resp = client.get("/?account=hpc")
        self.assertEqual(resp.status_code, 200)
        m_client.return_value.stats.assert_called_once_with(
            None, "hpc", show_all=True, include_breakdown=True
        )

    def test_username_and_account_are_mutually_exclusive(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            client = web_app().test_client()
            resp = client.get("/?username=alice&account=hpc")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("mutually exclusive", body)
        m_client.return_value.stats.assert_not_called()

    def test_urlerror_is_rendered_in_page(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.stats.side_effect = ServiceUnreachableError("boom")
            client = web_app().test_client()
            resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("Failed to retrieve stats", body)

    def test_unit_hours_displays_decimal_hours(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            resp = client.get("/?unit=hours")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("2.00", body)
        self.assertIn('name="unit"', body)
        self.assertIn('value="hours"', body)

    def test_unit_seconds_displays_seconds(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            resp = client.get("/?unit=seconds")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("7200", body)
        self.assertIn("36000", body)
        self.assertRegex(body, r'value="seconds"\s+checked')

    def test_unit_seconds_is_restored_from_session(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            client.get("/?unit=s")
            resp = client.get("/")
        body = resp.get_data(as_text=True)
        self.assertRegex(body, r'value="seconds"\s+checked')
        self.assertIn("36000", body)

    def test_default_unit_is_minutes(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            resp = client.get("/")
        body = resp.get_data(as_text=True)
        self.assertRegex(body, r'value="minutes"\s+checked')

    def test_dashboard_restores_filters_from_session(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            client.get("/?username=alice&unit=hours")
            m_client.return_value.stats.reset_mock()
            client.get("/")
        m_client.return_value.stats.assert_called_once_with(
            "alice", None, show_all=False, include_breakdown=True
        )

    def test_dashboard_persists_account_filter_to_session(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            client.get("/?account=hpc")
            m_client.return_value.stats.reset_mock()
            resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        m_client.return_value.stats.assert_called_once_with(
            None, "hpc", show_all=True, include_breakdown=True
        )


class TestAuthRoutes(SlurmQuotaTestCase):
    def setUp(self):
        super().setUp()
        configure_web_app()

    def _mock_login_client(self) -> MagicMock:
        mock_api = MagicMock()

        def _login(username, password):
            mock_api.token = "jwt-token"
            return {"token": "jwt-token", "username": username, "role": "admin"}

        mock_api.login.side_effect = _login
        return mock_api

    def test_login_post_api_unreachable_shows_error_page(self):
        with patch("slurm_quota.web.routes.APIClient") as m_client_cls:
            m_client_cls.return_value.login.side_effect = ServiceUnreachableError(
                "boom"
            )
            client = web_app().test_client()
            login_page = client.get("/login")
            csrf = extract_csrf(login_page.get_data(as_text=True))
            resp = client.post(
                "/login",
                data={
                    "_csrf": csrf,
                    "username": "alice",
                    "password": "secret",
                },
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("Login failed", body)
        self.assertIn("boom", body)

    def test_login_success_sets_session_and_renders_dashboard(self):
        mock_api = self._mock_login_client()
        with (
            auth_enabled(),
            patch("slurm_quota.web.routes.APIClient", return_value=mock_api),
            patch("slurm_quota.web.routes.api_client") as m_client,
            patch("slurm_quota.web.routes.current_role", return_value="admin"),
        ):
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            login_page = client.get("/login")
            csrf = extract_csrf(login_page.get_data(as_text=True))
            resp = client.post(
                "/login",
                data={
                    "_csrf": csrf,
                    "username": "alice",
                    "password": "secret",
                    "next": "/",
                },
                follow_redirects=False,
            )
            self.assertEqual(resp.status_code, 302)
            dashboard = client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("Signed in as", dashboard.get_data(as_text=True))
        m_client.return_value.stats.assert_called_with(
            None, None, show_all=True, include_breakdown=True
        )

    def test_login_rejects_bad_credentials(self):
        mock_api = MagicMock()
        mock_api.login.side_effect = ServiceHTTPError(401)
        with (
            auth_enabled(),
            patch("slurm_quota.web.routes.APIClient", return_value=mock_api),
        ):
            client = web_app().test_client()
            login_page = client.get("/login")
            csrf = extract_csrf(login_page.get_data(as_text=True))
            resp = client.post(
                "/login",
                data={
                    "_csrf": csrf,
                    "username": "alice",
                    "password": "wrong",
                    "next": "/",
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Invalid username or password", resp.get_data(as_text=True))

    def test_logout_clears_session(self):
        mock_api = self._mock_login_client()
        with (
            auth_enabled(),
            patch("slurm_quota.web.routes.APIClient", return_value=mock_api),
            patch("slurm_quota.web.routes.api_client") as m_client,
            patch("slurm_quota.web.routes.current_role", return_value="admin"),
        ):
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            login_page = client.get("/login")
            csrf = extract_csrf(login_page.get_data(as_text=True))
            client.post(
                "/login",
                data={
                    "_csrf": csrf,
                    "username": "alice",
                    "password": "secret",
                    "next": "/",
                },
            )
            dashboard = client.get("/")
            csrf = extract_csrf(dashboard.get_data(as_text=True))
            logout = client.post(
                "/logout",
                data={"_csrf": csrf},
                follow_redirects=False,
            )
            self.assertEqual(logout.status_code, 302)
            redirect = client.get("/")
        self.assertEqual(redirect.status_code, 302)
        self.assertIn("/login", redirect.headers["Location"])

    def test_expired_token_redirects_to_login(self):
        mock_api = self._mock_login_client()
        with (
            auth_enabled(),
            patch("slurm_quota.web.routes.APIClient", return_value=mock_api),
            patch("slurm_quota.web.routes.api_client") as m_client,
            patch("slurm_quota.web.routes.current_role", return_value="admin"),
        ):
            m_client.return_value.stats.side_effect = ServiceHTTPError(401)
            client = web_app().test_client()
            login_page = client.get("/login")
            csrf = extract_csrf(login_page.get_data(as_text=True))
            client.post(
                "/login",
                data={
                    "_csrf": csrf,
                    "username": "alice",
                    "password": "secret",
                    "next": "/",
                },
            )
            resp = client.get("/", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("session_expired", resp.headers["Location"])

    def test_session_expired_message_on_login_page(self):
        client = web_app().test_client()
        resp = client.get("/login?message=session_expired")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("session has expired", resp.get_data(as_text=True))

    def test_login_redirects_to_mount_prefix_after_success(self):
        mock_api = self._mock_login_client()
        base_url = "http://localhost/quota"
        with (
            auth_enabled(),
            patch("slurm_quota.web.routes.APIClient", return_value=mock_api),
            patch("slurm_quota.web.routes.api_client") as m_client,
            patch("slurm_quota.web.routes.current_role", return_value="admin"),
        ):
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            login_redirect = client.get("/", base_url=base_url, follow_redirects=False)
            self.assertEqual(login_redirect.status_code, 302)
            self.assertIn("/quota/login", login_redirect.headers["Location"])
            self.assertIn("next=%2Fquota%2F", login_redirect.headers["Location"])

            login_page = client.get(
                "/login",
                base_url=base_url,
                query_string={"next": "/quota/"},
            )
            csrf = extract_csrf(login_page.get_data(as_text=True))
            resp = client.post(
                "/login",
                base_url=base_url,
                data={
                    "_csrf": csrf,
                    "username": "alice",
                    "password": "secret",
                    "next": "/quota/",
                },
                follow_redirects=False,
            )
            self.assertEqual(resp.status_code, 302)
            # Compare path only: Werkzeug <2.1 autocorrects Location to absolute URLs.
            self.assertEqual(redirect_path(resp.headers["Location"]), "/quota/")

            dashboard = client.get("/", base_url=base_url)
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("Signed in as", dashboard.get_data(as_text=True))

    def test_login_post_with_unprefixed_next_under_mount(self):
        mock_api = self._mock_login_client()
        base_url = "http://localhost/quota"
        with (
            auth_enabled(),
            patch("slurm_quota.web.routes.APIClient", return_value=mock_api),
        ):
            client = web_app().test_client()
            login_page = client.get("/login", base_url=base_url)
            csrf = extract_csrf(login_page.get_data(as_text=True))
            resp = client.post(
                "/login",
                base_url=base_url,
                data={
                    "_csrf": csrf,
                    "username": "alice",
                    "password": "secret",
                    "next": "/",
                },
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 302)
        # Compare path only: Werkzeug <2.1 autocorrects Location to absolute URLs.
        self.assertEqual(redirect_path(resp.headers["Location"]), "/quota/")


class TestRolesRoutes(SlurmQuotaTestCase):
    def setUp(self):
        super().setUp()
        configure_web_app()

    def test_roles_lists_users_for_admin(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.users_roles.return_value = roles_users()
            client = web_app().test_client()
            resp = client.get("/roles")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("slurm-quota", body)
        self.assertIn("Back to dashboard", body)
        self.assertIn("bob", body)
        self.assertIn("carol", body)
        self.assertIn('id="role-change-dialog"', body)
        self.assertIn('id="manager-account-add-dialog"', body)
        self.assertIn('data-action="role-change"', body)
        self.assertNotIn('<button type="submit">Grant operator</button>', body)
        m_client.return_value.users_roles.assert_called_once()

    def test_roles_redirects_non_admin_to_dashboard(self):
        with (
            patch(
                "slurm_quota.web.app.ClientToken.load_value", return_value="test-token"
            ),
            patch("slurm_quota.web.routes.current_role", return_value="user"),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            client = web_app().test_client()
            resp = client.get("/roles", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["Location"].endswith("/"))
        m_client.return_value.users_roles.assert_not_called()

    def test_roles_renders_api_error(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.users_roles.side_effect = ServiceHTTPError(500)
            client = web_app().test_client()
            resp = client.get("/roles")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Failed to retrieve roles", resp.get_data(as_text=True))

    def test_roles_forbidden_clears_session_and_redirects_to_login(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.users_roles.side_effect = ServiceHTTPError(403)
            client = web_app().test_client()
            with client.session_transaction() as sess:
                sess["token"] = "jwt-token"
            resp = client.get("/roles", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("session_expired", resp.headers["Location"])
        with client.session_transaction() as sess:
            self.assertNotIn("token", sess)

    def test_roles_post_grant_operator_calls_api(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.users_roles.return_value = roles_users()
            client = web_app().test_client()
            roles_page = client.get("/roles")
            csrf = extract_csrf(roles_page.get_data(as_text=True))
            resp = client.post(
                "/roles",
                data={
                    "_csrf": csrf,
                    "action": "grant",
                    "role": "operator",
                    "username": "bob",
                },
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/roles", resp.headers["Location"])
        m_client.return_value.grant_role.assert_called_once_with("operator", "bob")

    def test_roles_post_grant_shows_success_message(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.users_roles.return_value = roles_users()
            client = web_app().test_client()
            roles_page = client.get("/roles")
            csrf = extract_csrf(roles_page.get_data(as_text=True))
            resp = client.post(
                "/roles",
                data={
                    "_csrf": csrf,
                    "action": "grant",
                    "role": "operator",
                    "username": "bob",
                },
                follow_redirects=True,
            )
        self.assertIn("Role updated for bob.", resp.get_data(as_text=True))

    def test_roles_post_grant_failure_shows_error(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.users_roles.return_value = roles_users()
            m_client.return_value.grant_role.side_effect = ServiceHTTPError(409)
            client = web_app().test_client()
            roles_page = client.get("/roles")
            csrf = extract_csrf(roles_page.get_data(as_text=True))
            resp = client.post(
                "/roles",
                data={
                    "_csrf": csrf,
                    "action": "grant",
                    "role": "operator",
                    "username": "bob",
                },
                follow_redirects=True,
            )
        self.assertIn("Failed to update role: HTTP 409", resp.get_data(as_text=True))

    def test_roles_post_revoke_manager_calls_api(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.users_roles.return_value = roles_users()
            client = web_app().test_client()
            roles_page = client.get("/roles")
            csrf = extract_csrf(roles_page.get_data(as_text=True))
            resp = client.post(
                "/roles",
                data={
                    "_csrf": csrf,
                    "action": "revoke",
                    "role": "manager",
                    "username": "carol",
                },
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 302)
        m_client.return_value.revoke_role.assert_called_once_with("manager", "carol")

    def test_roles_post_add_manager_account_calls_api(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.users_roles.return_value = [
                {"username": "carol", "role": "manager", "accounts": []},
            ]
            client = web_app().test_client()
            roles_page = client.get("/roles")
            csrf = extract_csrf(roles_page.get_data(as_text=True))
            resp = client.post(
                "/roles/managers/accounts",
                data={
                    "_csrf": csrf,
                    "action": "add",
                    "username": "carol",
                    "account": "hpc",
                },
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 302)
        m_client.return_value.add_manager_account.assert_called_once_with(
            "carol", "hpc"
        )

    def test_roles_post_redirects_non_admin_to_dashboard(self):
        with (
            patch(
                "slurm_quota.web.app.ClientToken.load_value", return_value="test-token"
            ),
            patch("slurm_quota.web.routes.current_role", return_value="user"),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            client = web_app().test_client()
            resp = client.post(
                "/roles",
                data={
                    "_csrf": "token",
                    "action": "grant",
                    "role": "operator",
                    "username": "bob",
                },
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["Location"].endswith("/"))
        m_client.return_value.grant_role.assert_not_called()

    def test_roles_post_rejects_invalid_csrf(self):
        with auth_disabled():
            client = web_app().test_client()
            resp = client.post(
                "/roles",
                data={
                    "_csrf": "wrong",
                    "action": "grant",
                    "role": "operator",
                    "username": "bob",
                },
            )
        self.assertEqual(resp.status_code, 400)

    def test_roles_post_empty_username_redirects_to_roles(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.users_roles.return_value = roles_users()
            client = web_app().test_client()
            roles_page = client.get("/roles")
            csrf = extract_csrf(roles_page.get_data(as_text=True))
            resp = client.post(
                "/roles",
                data={
                    "_csrf": csrf,
                    "action": "grant",
                    "role": "operator",
                    "username": "",
                },
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/roles", resp.headers["Location"])
        m_client.return_value.grant_role.assert_not_called()

    def test_roles_post_forbidden_clears_session_and_redirects_to_login(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.users_roles.return_value = roles_users()
            m_client.return_value.grant_role.side_effect = ServiceHTTPError(403)
            client = web_app().test_client()
            roles_page = client.get("/roles")
            csrf = extract_csrf(roles_page.get_data(as_text=True))
            with client.session_transaction() as sess:
                sess["token"] = "jwt-token"
            resp = client.post(
                "/roles",
                data={
                    "_csrf": csrf,
                    "action": "grant",
                    "role": "operator",
                    "username": "bob",
                },
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("session_expired", resp.headers["Location"])
        with client.session_transaction() as sess:
            self.assertNotIn("token", sess)


class TestQuotasRoutes(SlurmQuotaTestCase):
    def setUp(self):
        super().setUp()
        configure_web_app()

    def test_dashboard_shows_quota_edit_for_admin(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('data-edit="quota"', body)
        self.assertIn('action="/quotas"', body)
        self.assertIn('name="quota_minutes"', body)

    def test_dashboard_shows_quota_edit_for_operator(self):
        with (
            patch(
                "slurm_quota.web.app.ClientToken.load_value", return_value="test-token"
            ),
            patch("slurm_quota.web.routes.current_role", return_value="operator"),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('data-edit="quota"', body)
        self.assertIn('action="/quotas"', body)
        self.assertIn('name="quota_minutes"', body)
        self.assertIn(">Edit</button>", body)

    def test_dashboard_hides_quota_edit_for_user(self):
        with (
            patch(
                "slurm_quota.web.app.ClientToken.load_value", return_value="test-token"
            ),
            patch("slurm_quota.web.routes.current_role", return_value="user"),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertNotIn('action="/quotas"', body)

    def test_quotas_post_calls_api_for_operator(self):
        with (
            patch(
                "slurm_quota.web.app.ClientToken.load_value", return_value="test-token"
            ),
            patch("slurm_quota.web.routes.current_role", return_value="operator"),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            dashboard = client.get("/")
            csrf = extract_csrf(dashboard.get_data(as_text=True))
            resp = client.post(
                "/quotas",
                data={
                    "_csrf": csrf,
                    "target": "user",
                    "name": "alice",
                    "resource": "cpu",
                    "quota_minutes": "900",
                },
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 302)
        m_client.return_value.set_user_cpu_quota.assert_called_once_with(
            "alice", 900, reason=None, append_reason=False
        )

    def test_quotas_post_forwards_reason_when_provided(self):
        with (
            patch(
                "slurm_quota.web.app.ClientToken.load_value", return_value="test-token"
            ),
            patch("slurm_quota.web.routes.current_role", return_value="operator"),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            dashboard = client.get("/")
            csrf = extract_csrf(dashboard.get_data(as_text=True))
            client.post(
                "/quotas",
                data={
                    "_csrf": csrf,
                    "target": "user",
                    "name": "alice",
                    "resource": "cpu",
                    "quota_minutes": "900",
                    "reason": "project extension approved",
                    "append_reason": "on",
                },
                follow_redirects=False,
            )
        m_client.return_value.set_user_cpu_quota.assert_called_once_with(
            "alice", 900, reason="project extension approved", append_reason=True
        )

    def test_quotas_post_shows_success_message(self):
        with (
            patch(
                "slurm_quota.web.app.ClientToken.load_value", return_value="test-token"
            ),
            patch("slurm_quota.web.routes.current_role", return_value="operator"),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            dashboard = client.get("/")
            csrf = extract_csrf(dashboard.get_data(as_text=True))
            resp = client.post(
                "/quotas",
                data={
                    "_csrf": csrf,
                    "target": "user",
                    "name": "alice",
                    "resource": "cpu",
                    "quota_minutes": "900",
                },
                follow_redirects=True,
            )
        self.assertIn("Quota updated for alice.", resp.get_data(as_text=True))

    def test_quotas_post_invalid_quota_shows_error(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            dashboard = client.get("/")
            csrf = extract_csrf(dashboard.get_data(as_text=True))
            resp = client.post(
                "/quotas",
                data={
                    "_csrf": csrf,
                    "target": "user",
                    "name": "alice",
                    "resource": "cpu",
                    "quota_minutes": "not-a-number",
                },
                follow_redirects=True,
            )
        self.assertIn("Invalid quota value.", resp.get_data(as_text=True))

    def test_quotas_post_preserves_filters_from_session(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            client.get("/?account=hpc")
            dashboard = client.get("/")
            csrf = extract_csrf(dashboard.get_data(as_text=True))
            m_client.return_value.stats.reset_mock()
            client.post(
                "/quotas",
                data={
                    "_csrf": csrf,
                    "target": "account",
                    "name": "hpc",
                    "resource": "cpu",
                    "quota_minutes": "900",
                },
                follow_redirects=True,
            )
        m_client.return_value.stats.assert_called_with(
            None, "hpc", show_all=True, include_breakdown=True
        )

    def test_quotas_post_unlimited_sets_minus_one(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            dashboard = client.get("/")
            csrf = extract_csrf(dashboard.get_data(as_text=True))
            client.post(
                "/quotas",
                data={
                    "_csrf": csrf,
                    "target": "account",
                    "name": "hpc",
                    "resource": "gpu",
                    "unlimited": "on",
                },
                follow_redirects=False,
            )
        m_client.return_value.set_account_gpu_quota.assert_called_once_with(
            "hpc", -1, reason=None, append_reason=False
        )

    def test_quotas_post_redirects_user_to_dashboard(self):
        with (
            patch(
                "slurm_quota.web.app.ClientToken.load_value", return_value="test-token"
            ),
            patch("slurm_quota.web.routes.current_role", return_value="user"),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            client = web_app().test_client()
            resp = client.post(
                "/quotas",
                data={
                    "_csrf": "token",
                    "target": "user",
                    "name": "alice",
                    "resource": "cpu",
                    "quota_minutes": "100",
                },
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["Location"].endswith("/"))
        m_client.return_value.set_user_cpu_quota.assert_not_called()

    def test_quotas_post_rejects_invalid_csrf(self):
        with auth_disabled():
            client = web_app().test_client()
            resp = client.post(
                "/quotas",
                data={
                    "_csrf": "wrong",
                    "target": "user",
                    "name": "alice",
                    "resource": "cpu",
                    "quota_minutes": "100",
                },
            )
        self.assertEqual(resp.status_code, 400)

    def test_quotas_post_forbidden_clears_session_and_redirects_to_login(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.stats.return_value = stats_rows()
            m_client.return_value.set_user_cpu_quota.side_effect = ServiceHTTPError(403)
            client = web_app().test_client()
            dashboard = client.get("/")
            csrf = extract_csrf(dashboard.get_data(as_text=True))
            with client.session_transaction() as sess:
                sess["token"] = "jwt-token"
            resp = client.post(
                "/quotas",
                data={
                    "_csrf": csrf,
                    "target": "user",
                    "name": "alice",
                    "resource": "cpu",
                    "quota_minutes": "100",
                },
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("session_expired", resp.headers["Location"])
        with client.session_transaction() as sess:
            self.assertNotIn("token", sess)


class TestCommentsRoutes(SlurmQuotaTestCase):
    def setUp(self):
        super().setUp()
        configure_web_app()

    def _dashboard(self, role: str, comments: bool = True) -> str:
        users, accounts, breakdown = stats_rows()
        if comments:
            users[0]["comment"] = "PhD <student>"
            accounts[0]["comment"] = "Project X"
        with (
            patch(
                "slurm_quota.web.app.ClientToken.load_value", return_value="test-token"
            ),
            patch("slurm_quota.web.routes.current_role", return_value=role),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            m_client.return_value.stats.return_value = (users, accounts, breakdown)
            resp = web_app().test_client().get("/")
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    def test_dashboard_shows_comments_to_users(self):
        body = self._dashboard("user")
        # Comments are escaped
        self.assertIn('<span class="row-comment-text">PhD &lt;student&gt;</span>', body)
        self.assertIn('<span class="row-comment-text">Project X</span>', body)
        self.assertNotIn('aria-label="Edit comment of', body)
        self.assertNotIn('action="/comments"', body)
        self.assertNotIn('name="append_reason"', body)

    def test_dashboard_shows_comment_edit_for_operator(self):
        body = self._dashboard("operator")
        self.assertIn('data-edit="comment"', body)
        self.assertIn('data-comment="Project X"', body)
        self.assertIn('action="/comments"', body)
        self.assertEqual(body.count('name="append_reason" checked'), 2)

    def test_dashboard_offers_add_comment_without_comment(self):
        body = self._dashboard("admin", comments=False)
        self.assertNotIn('class="row-comment-text"', body)
        self.assertIn(">Add comment</button>", body)

    def _post(self, role: str, data: dict):
        with (
            patch(
                "slurm_quota.web.app.ClientToken.load_value", return_value="test-token"
            ),
            patch("slurm_quota.web.routes.current_role", return_value=role),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            csrf = extract_csrf(client.get("/").get_data(as_text=True))
            resp = client.post(
                "/comments", data={"_csrf": csrf, **data}, follow_redirects=True
            )
        return resp, m_client.return_value

    def test_comments_post_sets_user_comment(self):
        resp, api = self._post(
            "operator", {"target": "user", "name": "alice", "comment": " PhD "}
        )
        api.set_user_comment.assert_called_once_with("alice", "PhD")
        self.assertIn("Comment updated for alice.", resp.get_data(as_text=True))

    def test_comments_post_clears_account_comment(self):
        resp, api = self._post(
            "admin", {"target": "account", "name": "hpc", "comment": ""}
        )
        api.set_account_comment.assert_called_once_with("hpc", None)
        self.assertIn("Comment removed for hpc.", resp.get_data(as_text=True))

    def test_comments_post_rejects_invalid_target(self):
        resp, api = self._post(
            "admin", {"target": "group", "name": "hpc", "comment": "x"}
        )
        api.set_account_comment.assert_not_called()
        api.set_user_comment.assert_not_called()
        self.assertIn("Invalid comment target.", resp.get_data(as_text=True))

    def test_comments_post_ignored_for_user(self):
        with (
            patch(
                "slurm_quota.web.app.ClientToken.load_value", return_value="test-token"
            ),
            patch("slurm_quota.web.routes.current_role", return_value="user"),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            resp = (
                web_app()
                .test_client()
                .post(
                    "/comments",
                    data={"target": "user", "name": "alice", "comment": "x"},
                    follow_redirects=False,
                )
            )
        self.assertEqual(resp.status_code, 302)
        m_client.return_value.set_user_comment.assert_not_called()

    def test_comments_post_reports_api_error(self):
        with (
            patch(
                "slurm_quota.web.app.ClientToken.load_value", return_value="test-token"
            ),
            patch("slurm_quota.web.routes.current_role", return_value="admin"),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            m_client.return_value.stats.return_value = stats_rows()
            m_client.return_value.set_user_comment.side_effect = ServiceHTTPError(
                400, message="comment must be a single line"
            )
            client = web_app().test_client()
            csrf = extract_csrf(client.get("/").get_data(as_text=True))
            resp = client.post(
                "/comments",
                data={"_csrf": csrf, "target": "user", "name": "alice", "comment": "x"},
                follow_redirects=True,
            )
        self.assertIn("Failed to update comment", resp.get_data(as_text=True))


class TestConsumptionRoutes(SlurmQuotaTestCase):
    def setUp(self):
        super().setUp()
        configure_web_app()

    def test_dashboard_shows_consumption_edit_for_admin(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('data-edit="consumption"', body)
        self.assertIn('action="/consumption"', body)
        self.assertIn('name="delta"', body)
        self.assertIn('name="delta_unit"', body)
        self.assertIn('name="username"', body)
        self.assertIn('name="account"', body)

    def test_dashboard_shows_consumption_edit_for_operator(self):
        with (
            patch(
                "slurm_quota.web.app.ClientToken.load_value", return_value="test-token"
            ),
            patch("slurm_quota.web.routes.current_role", return_value="operator"),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('data-edit="consumption"', body)
        self.assertIn('action="/consumption"', body)
        self.assertIn('name="delta"', body)
        self.assertIn('name="delta_unit"', body)
        self.assertIn(">Edit</button>", body)

    def test_dashboard_hides_consumption_edit_for_user(self):
        with (
            patch(
                "slurm_quota.web.app.ClientToken.load_value", return_value="test-token"
            ),
            patch("slurm_quota.web.routes.current_role", return_value="user"),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertNotIn('action="/consumption"', body)

    def test_consumption_post_calls_api_for_operator(self):
        with (
            patch(
                "slurm_quota.web.app.ClientToken.load_value", return_value="test-token"
            ),
            patch("slurm_quota.web.routes.current_role", return_value="operator"),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            dashboard = client.get("/")
            csrf = extract_csrf(dashboard.get_data(as_text=True))
            resp = client.post(
                "/consumption",
                data={
                    "_csrf": csrf,
                    "username": "alice",
                    "account": "hpc",
                    "resource": "cpu",
                    "delta": "-15",
                },
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 302)
        m_client.return_value.adjust_consumption.assert_called_once_with(
            "alice", "hpc", "cpu", -900, reason=None, append_reason=False
        )

    def _post_consumption(self, m_client, data):
        m_client.return_value.stats.return_value = stats_rows()
        client = web_app().test_client()
        dashboard = client.get("/")
        csrf = extract_csrf(dashboard.get_data(as_text=True))
        return client.post(
            "/consumption",
            data={
                "_csrf": csrf,
                "username": "alice",
                "account": "hpc",
                "resource": "cpu",
                **data,
            },
            follow_redirects=True,
        )

    def test_consumption_post_delta_in_seconds(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            self._post_consumption(m_client, {"delta": "-45", "delta_unit": "seconds"})
        m_client.return_value.adjust_consumption.assert_called_once_with(
            "alice", "hpc", "cpu", -45, reason=None, append_reason=False
        )

    def test_consumption_post_delta_in_minutes(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            self._post_consumption(m_client, {"delta": "2", "delta_unit": "minutes"})
        m_client.return_value.adjust_consumption.assert_called_once_with(
            "alice", "hpc", "cpu", 120, reason=None, append_reason=False
        )

    def test_consumption_post_legacy_delta_minutes(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            self._post_consumption(m_client, {"delta_minutes": "3"})
        m_client.return_value.adjust_consumption.assert_called_once_with(
            "alice", "hpc", "cpu", 180, reason=None, append_reason=False
        )

    def test_consumption_post_rejects_invalid_unit(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            resp = self._post_consumption(
                m_client, {"delta": "3", "delta_unit": "days"}
            )
        m_client.return_value.adjust_consumption.assert_not_called()
        self.assertIn(
            "Invalid consumption adjustment unit.", resp.get_data(as_text=True)
        )

    def test_consumption_post_forwards_reason_when_provided(self):
        with (
            patch(
                "slurm_quota.web.app.ClientToken.load_value", return_value="test-token"
            ),
            patch("slurm_quota.web.routes.current_role", return_value="operator"),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            dashboard = client.get("/")
            csrf = extract_csrf(dashboard.get_data(as_text=True))
            client.post(
                "/consumption",
                data={
                    "_csrf": csrf,
                    "username": "alice",
                    "account": "hpc",
                    "resource": "cpu",
                    "delta": "-15",
                    "reason": "correct billing error",
                    "append_reason": "on",
                },
                follow_redirects=False,
            )
        m_client.return_value.adjust_consumption.assert_called_once_with(
            "alice",
            "hpc",
            "cpu",
            -900,
            reason="correct billing error",
            append_reason=True,
        )

    def test_consumption_post_shows_success_message(self):
        with (
            patch(
                "slurm_quota.web.app.ClientToken.load_value", return_value="test-token"
            ),
            patch("slurm_quota.web.routes.current_role", return_value="operator"),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            dashboard = client.get("/")
            csrf = extract_csrf(dashboard.get_data(as_text=True))
            resp = client.post(
                "/consumption",
                data={
                    "_csrf": csrf,
                    "username": "alice",
                    "account": "hpc",
                    "resource": "cpu",
                    "delta": "-15",
                },
                follow_redirects=True,
            )
        self.assertIn(
            "Consumption adjusted for user alice on account hpc.",
            resp.get_data(as_text=True),
        )

    def test_consumption_post_rejects_missing_account(self):
        with (
            patch(
                "slurm_quota.web.app.ClientToken.load_value", return_value="test-token"
            ),
            patch("slurm_quota.web.routes.current_role", return_value="operator"),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            dashboard = client.get("/")
            csrf = extract_csrf(dashboard.get_data(as_text=True))
            resp = client.post(
                "/consumption",
                data={
                    "_csrf": csrf,
                    "username": "alice",
                    "account": "  ",
                    "resource": "cpu",
                    "delta": "-15",
                },
                follow_redirects=True,
            )
        self.assertIn(
            "Invalid consumption user, account or resource.",
            resp.get_data(as_text=True),
        )
        m_client.return_value.adjust_consumption.assert_not_called()

    def test_consumption_post_failure_shows_error(self):
        with (
            patch(
                "slurm_quota.web.app.ClientToken.load_value", return_value="test-token"
            ),
            patch("slurm_quota.web.routes.current_role", return_value="operator"),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            m_client.return_value.stats.return_value = stats_rows()
            m_client.return_value.adjust_consumption.side_effect = ServiceHTTPError(500)
            client = web_app().test_client()
            dashboard = client.get("/")
            csrf = extract_csrf(dashboard.get_data(as_text=True))
            resp = client.post(
                "/consumption",
                data={
                    "_csrf": csrf,
                    "username": "alice",
                    "account": "hpc",
                    "resource": "cpu",
                    "delta": "-15",
                },
                follow_redirects=True,
            )
        self.assertIn(
            "Failed to adjust consumption: HTTP 500", resp.get_data(as_text=True)
        )

    def test_consumption_post_failure_shows_api_message(self):
        with (
            patch(
                "slurm_quota.web.app.ClientToken.load_value", return_value="test-token"
            ),
            patch("slurm_quota.web.routes.current_role", return_value="operator"),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            m_client.return_value.stats.return_value = stats_rows()
            m_client.return_value.adjust_consumption.side_effect = ServiceHTTPError(
                400, message="Invalid account"
            )
            client = web_app().test_client()
            dashboard = client.get("/")
            csrf = extract_csrf(dashboard.get_data(as_text=True))
            resp = client.post(
                "/consumption",
                data={
                    "_csrf": csrf,
                    "username": "alice",
                    "account": "hpc",
                    "resource": "cpu",
                    "delta": "-15",
                },
                follow_redirects=True,
            )
        self.assertIn(
            "Failed to adjust consumption: HTTP 400: Invalid account",
            resp.get_data(as_text=True),
        )

    def test_consumption_post_redirects_user_to_dashboard(self):
        with (
            patch(
                "slurm_quota.web.app.ClientToken.load_value", return_value="test-token"
            ),
            patch("slurm_quota.web.routes.current_role", return_value="user"),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            client = web_app().test_client()
            resp = client.post(
                "/consumption",
                data={
                    "_csrf": "token",
                    "username": "alice",
                    "account": "hpc",
                    "resource": "cpu",
                    "delta": "10",
                },
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 302)
        m_client.return_value.adjust_consumption.assert_not_called()

    def test_consumption_post_rejects_invalid_csrf(self):
        with auth_disabled():
            client = web_app().test_client()
            resp = client.post(
                "/consumption",
                data={
                    "_csrf": "wrong",
                    "username": "alice",
                    "account": "hpc",
                    "resource": "cpu",
                    "delta": "10",
                },
            )
        self.assertEqual(resp.status_code, 400)


class TestDashboardBreakdown(SlurmQuotaTestCase):
    def setUp(self):
        super().setUp()
        configure_web_app()

    def _get(self, path: str = "/", role: str = "admin") -> str:
        with (
            patch(
                "slurm_quota.web.app.ClientToken.load_value", return_value="test-token"
            ),
            patch("slurm_quota.web.routes.current_role", return_value=role),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            m_client.return_value.stats.return_value = stats_rows_with_breakdown()
            resp = web_app().test_client().get(path)
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    def test_unfiltered_breaks_down_users_and_accounts(self):
        body = self._get("/")
        self.assertIn('aria-label="Show alice consumption by account"', body)
        self.assertIn('aria-label="Show hpc consumption by user"', body)
        # alice by hpc and (unattributed), hpc by alice and bob
        self.assertEqual(body.count('class="breakdown-row"'), 4)
        self.assertIn('title="(unattributed)">(unattributed)</td>', body)

    def test_username_filter_breaks_down_users_only(self):
        body = self._get("/?username=alice")
        self.assertIn("Show alice consumption by account", body)
        self.assertNotIn("Show hpc consumption by user", body)

    def test_account_filter_breaks_down_accounts_only(self):
        body = self._get("/?account=hpc")
        self.assertNotIn("Show alice consumption by account", body)
        self.assertIn("Show hpc consumption by user", body)

    def test_breakdown_rows_are_hidden_until_expanded(self):
        body = self._get("/")
        self.assertIn(
            '<tr class="breakdown-row" data-breakdown-group="user-1" hidden>', body
        )

    def test_breakdown_rows_have_pair_edit_for_operator(self):
        body = self._get("/", role="operator")
        self.assertIn('data-username="alice"', body)
        self.assertIn('data-account="hpc"', body)
        self.assertIn('aria-label="Adjust cpu consumption of alice on hpc"', body)
        self.assertIn('<script type="application/json" id="breakdown-data">', body)

    def test_breakdown_rows_without_edit_for_user(self):
        body = self._get("/", role="user")
        self.assertIn('class="breakdown-row"', body)
        self.assertNotIn("data-username=", body)
        self.assertNotIn('id="breakdown-data"', body)


def _app_tokens() -> list[dict]:
    return [
        {
            "id": "abc123",
            "username": "bob",
            "name": "ci",
            "scopes": ["stats:read:own"],
            "created_at": 1_700_000_000,
            "expires_at": 1_707_776_000,
            "revoked_at": None,
            "status": "active",
        },
        {
            "id": "def456",
            "username": "bob",
            "name": "old",
            "scopes": ["stats:read:own"],
            "created_at": 1_600_000_000,
            "expires_at": 1_607_776_000,
            "revoked_at": 1_600_000_100,
            "status": "revoked",
        },
    ]


class TestTokensRoutes(SlurmQuotaTestCase):
    def setUp(self):
        super().setUp()
        configure_web_app()

    def test_tokens_lists_tokens_and_role_scopes(self):
        with (
            auth_disabled(),
            patch("slurm_quota.web.routes.current_role", return_value="operator"),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            m_client.return_value.list_app_tokens.return_value = _app_tokens()
            client = web_app().test_client()
            resp = client.get("/tokens")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("abc123", body)
        self.assertIn("def456", body)
        self.assertEqual(body.count('name="token_id"'), 1)
        self.assertIn('value="quotas:write"', body)
        self.assertNotIn('value="roles:write"', body)

    def test_tokens_redirects_without_role(self):
        with (
            patch(
                "slurm_quota.web.app.ClientToken.load_value", return_value="test-token"
            ),
            patch("slurm_quota.web.routes.current_role", return_value=None),
            patch("slurm_quota.web.routes.api_client") as m_client,
        ):
            client = web_app().test_client()
            resp = client.get("/tokens", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        m_client.return_value.list_app_tokens.assert_not_called()

    def test_tokens_unauthorized_clears_session(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.list_app_tokens.side_effect = ServiceHTTPError(401)
            client = web_app().test_client()
            resp = client.get("/tokens", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("session_expired", resp.headers["Location"])

    def test_tokens_post_create_shows_token_once(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.list_app_tokens.return_value = []
            m_client.return_value.create_app_token.return_value = {
                **_app_tokens()[0],
                "token": "secret-app-jwt",
            }
            client = web_app().test_client()
            csrf = extract_csrf(client.get("/tokens").get_data(as_text=True))
            resp = client.post(
                "/tokens",
                data={
                    "_csrf": csrf,
                    "action": "create",
                    "name": "ci",
                    "scopes": ["stats:read:own", "quotas:read"],
                    "duration": "30",
                },
            )
            self.assertEqual(resp.status_code, 200)
            self.assertIn("secret-app-jwt", resp.get_data(as_text=True))
            again = client.get("/tokens")
        self.assertNotIn("secret-app-jwt", again.get_data(as_text=True))
        m_client.return_value.create_app_token.assert_called_once_with(
            "ci", ["stats:read:own", "quotas:read"], 30
        )

    def test_tokens_post_create_reports_api_error(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.list_app_tokens.return_value = []
            m_client.return_value.create_app_token.side_effect = ServiceHTTPError(
                403, message="Scopes not allowed for user: quotas:write"
            )
            client = web_app().test_client()
            csrf = extract_csrf(client.get("/tokens").get_data(as_text=True))
            resp = client.post(
                "/tokens",
                data={
                    "_csrf": csrf,
                    "action": "create",
                    "name": "ci",
                    "scopes": ["quotas:write"],
                },
                follow_redirects=True,
            )
        self.assertIn(
            "Scopes not allowed for user: quotas:write", resp.get_data(as_text=True)
        )

    def test_tokens_post_rejects_invalid_duration(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.list_app_tokens.return_value = []
            client = web_app().test_client()
            csrf = extract_csrf(client.get("/tokens").get_data(as_text=True))
            resp = client.post(
                "/tokens",
                data={
                    "_csrf": csrf,
                    "action": "create",
                    "name": "ci",
                    "scopes": ["stats:read:own"],
                    "duration": "abc",
                },
                follow_redirects=True,
            )
        self.assertIn("Token duration must be a number", resp.get_data(as_text=True))
        m_client.return_value.create_app_token.assert_not_called()

    def test_tokens_post_revoke_calls_api(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.list_app_tokens.return_value = _app_tokens()
            client = web_app().test_client()
            csrf = extract_csrf(client.get("/tokens").get_data(as_text=True))
            resp = client.post(
                "/tokens",
                data={"_csrf": csrf, "action": "revoke", "token_id": "abc123"},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 302)
        m_client.return_value.revoke_app_token.assert_called_once_with("abc123")

    def test_tokens_post_requires_csrf(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client"):
            client = web_app().test_client()
            resp = client.post("/tokens", data={"action": "revoke", "token_id": "x"})
        self.assertEqual(resp.status_code, 400)

    def test_dashboard_links_tokens_page_when_logged_in(self):
        with auth_disabled(), patch("slurm_quota.web.routes.api_client") as m_client:
            m_client.return_value.stats.return_value = stats_rows()
            client = web_app().test_client()
            with client.session_transaction() as sess:
                sess["username"] = "alice"
            resp = client.get("/")
        self.assertIn("API tokens", resp.get_data(as_text=True))
