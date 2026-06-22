"""Unit tests for slurm_quota.client."""

from __future__ import annotations

import json
from unittest.mock import patch

from urllib.error import URLError

from slurm_quota.client import APIClient, ServiceHTTPError
from slurm_quota.token import save_service_token

from tests.test_support import SlurmQuotaTestCase


class _FakeUrlopenResponse:
    def __init__(self, payload=None, status=200):
        self.status = status
        self._payload = payload if payload is not None else {}

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _sample_payload():
    return {
        "users": [
            {
                "username": "alice",
                "job_count": 2,
                "last_updated": "2024-06-01T10:00:00",
                "total_consumed_cpu_minutes": 120,
                "total_preallocated_cpu_minutes": 60,
                "quota_cpu_minutes": 600,
                "total_consumed_gpu_minutes": 0,
                "total_preallocated_gpu_minutes": 0,
                "quota_gpu_minutes": -1,
            }
        ],
        "accounts": [
            {
                "account": "acct1",
                "job_count": 0,
                "last_updated": None,
                "total_consumed_cpu_minutes": 1,
                "total_preallocated_cpu_minutes": 0,
                "quota_cpu_minutes": -1,
                "total_consumed_gpu_minutes": 0,
                "total_preallocated_gpu_minutes": 0,
                "quota_gpu_minutes": -1,
            }
        ],
    }


class TestAPIClientStats(SlurmQuotaTestCase):
    def test_returns_users_and_accounts(self):
        payload = _sample_payload()
        with patch(
            "slurm_quota.client.urlopen", return_value=_FakeUrlopenResponse(payload)
        ):
            users, accounts = APIClient().stats("alice", None, False)
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["username"], "alice")
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["account"], "acct1")

    def test_empty_payload_lists_when_keys_missing(self):
        with patch("slurm_quota.client.urlopen", return_value=_FakeUrlopenResponse({})):
            users, accounts = APIClient().stats(None, None, True)
        self.assertEqual(users, [])
        self.assertEqual(accounts, [])

    def test_adds_username_query_when_filtered(self):
        with patch(
            "slurm_quota.client.urlopen",
            return_value=_FakeUrlopenResponse(_sample_payload()),
        ) as m_urlopen:
            APIClient().stats("alice", None, False)
        req = m_urlopen.call_args[0][0]
        self.assertIn("username=alice", req.full_url)

    def test_no_username_query_when_show_all(self):
        with patch(
            "slurm_quota.client.urlopen",
            return_value=_FakeUrlopenResponse(_sample_payload()),
        ) as m_urlopen:
            APIClient().stats("alice", None, True)
        req = m_urlopen.call_args[0][0]
        self.assertNotIn("username=", req.full_url)

    def test_adds_account_query_when_filtered(self):
        with patch(
            "slurm_quota.client.urlopen",
            return_value=_FakeUrlopenResponse(_sample_payload()),
        ) as m_urlopen:
            APIClient().stats(None, "projX", False)
        req = m_urlopen.call_args[0][0]
        self.assertIn("account=projX", req.full_url)

    def test_respects_base_url(self):
        with patch(
            "slurm_quota.client.urlopen",
            return_value=_FakeUrlopenResponse(_sample_payload()),
        ) as m_urlopen:
            APIClient(base_url="http://custom.example:9999/api/").stats(
                None, None, True
            )
        req = m_urlopen.call_args[0][0]
        self.assertTrue(
            req.full_url.startswith("http://custom.example:9999/api/"),
            req.full_url,
        )
        self.assertIn("/stats", req.full_url)

    def test_raises_stats_http_error_on_bad_status(self):
        with patch(
            "slurm_quota.client.urlopen",
            return_value=_FakeUrlopenResponse({}, status=500),
        ):
            with self.assertRaises(ServiceHTTPError) as cm:
                APIClient().stats(None, None, True)
        self.assertEqual(cm.exception.status, 500)

    def test_urlerror_propagates(self):
        with patch("slurm_quota.client.urlopen", side_effect=URLError("boom")):
            with self.assertRaises(URLError):
                APIClient().stats(None, None, True)

    def test_adds_authorization_header_when_token_set(self):
        with patch(
            "slurm_quota.client.urlopen",
            return_value=_FakeUrlopenResponse(_sample_payload()),
        ) as m_urlopen:
            APIClient(token="session-jwt").stats(None, None, True)
        req = m_urlopen.call_args[0][0]
        self.assertEqual(req.get_header("Authorization"), "Bearer session-jwt")

    def test_omits_authorization_header_when_token_is_none(self):
        self.env({"SLURM_QUOTA_TOKEN": "env-jwt"})
        with patch(
            "slurm_quota.client.urlopen",
            return_value=_FakeUrlopenResponse(_sample_payload()),
        ) as m_urlopen:
            APIClient(token=None).stats(None, None, True)
        req = m_urlopen.call_args[0][0]
        self.assertIsNone(req.get_header("Authorization"))


class TestAPIClientLogin(SlurmQuotaTestCase):
    def test_returns_payload_from_login_response(self):
        with patch(
            "slurm_quota.client.urlopen",
            return_value=_FakeUrlopenResponse(
                {"token": "jwt-token", "username": "alice", "role": "admin"}
            ),
        ) as m_urlopen:
            client = APIClient()
            payload = client.login("alice", "secret")
        self.assertEqual(payload["token"], "jwt-token")
        self.assertEqual(payload["role"], "admin")
        self.assertEqual(client.token, "jwt-token")
        req = m_urlopen.call_args[0][0]
        self.assertTrue(req.full_url.endswith("/login"))
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body, {"username": "alice", "password": "secret"})

    def test_respects_base_url(self):
        with patch(
            "slurm_quota.client.urlopen",
            return_value=_FakeUrlopenResponse({"token": "jwt-token"}),
        ) as m_urlopen:
            APIClient(base_url="http://custom.example:9999/api/").login(
                "alice", "secret"
            )
        req = m_urlopen.call_args[0][0]
        self.assertEqual(
            req.full_url,
            "http://custom.example:9999/api/login",
        )

    def test_raises_service_http_error_on_bad_status(self):
        with patch(
            "slurm_quota.client.urlopen",
            return_value=_FakeUrlopenResponse({}, status=401),
        ):
            with self.assertRaises(ServiceHTTPError) as cm:
                APIClient().login("alice", "wrong")
        self.assertEqual(cm.exception.status, 401)

    def test_raises_value_error_when_token_missing(self):
        with patch(
            "slurm_quota.client.urlopen",
            return_value=_FakeUrlopenResponse({}),
        ):
            with self.assertRaises(ValueError):
                APIClient().login("alice", "secret")

    def test_urlerror_propagates(self):
        with patch("slurm_quota.client.urlopen", side_effect=URLError("boom")):
            with self.assertRaises(URLError):
                APIClient().login("alice", "secret")

    def test_stats_uses_token_from_login(self):
        users_payload = _sample_payload()

        def urlopen_side_effect(request):
            if request.full_url.endswith("/login"):
                return _FakeUrlopenResponse({"token": "jwt-token"})
            return _FakeUrlopenResponse(users_payload)

        with patch(
            "slurm_quota.client.urlopen", side_effect=urlopen_side_effect
        ) as m_urlopen:
            client = APIClient()
            client.login("alice", "secret")
            client.stats(None, None, True)
        stats_req = m_urlopen.call_args[0][0]
        self.assertEqual(stats_req.get_header("Authorization"), "Bearer jwt-token")

    def test_cli_pattern_loads_token_from_file(self):
        config_home = self._tmp.name + "/xdg-config"
        self.env({"XDG_CONFIG_HOME": config_home})
        save_service_token("file-jwt")
        with patch(
            "slurm_quota.client.urlopen",
            return_value=_FakeUrlopenResponse(_sample_payload()),
        ) as m_urlopen:
            from slurm_quota.token import load_service_token

            APIClient(token=load_service_token()).stats(None, None, True)
        req = m_urlopen.call_args[0][0]
        self.assertEqual(req.get_header("Authorization"), "Bearer file-jwt")


class TestAPIClientRoles(SlurmQuotaTestCase):
    def test_me_returns_payload(self):
        with patch(
            "slurm_quota.client.urlopen",
            return_value=_FakeUrlopenResponse({"username": "alice", "role": "admin"}),
        ):
            payload = APIClient(token="jwt").me()
        self.assertEqual(payload["role"], "admin")

    def test_me_raises_service_http_error_on_bad_status(self):
        with patch(
            "slurm_quota.client.urlopen",
            return_value=_FakeUrlopenResponse({}, status=403),
        ):
            with self.assertRaises(ServiceHTTPError) as cm:
                APIClient(token="jwt").me()
        self.assertEqual(cm.exception.status, 403)

    def test_me_raises_value_error_when_token_missing(self):
        with self.assertRaises(ValueError):
            APIClient(token=None).me()

    def test_users_roles_returns_users(self):
        with patch(
            "slurm_quota.client.urlopen",
            return_value=_FakeUrlopenResponse(
                {"users": [{"username": "bob", "role": "user"}]}
            ),
        ):
            users = APIClient(token="jwt").users_roles()
        self.assertEqual(users[0]["username"], "bob")

    def test_users_roles_raises_service_http_error_on_bad_status(self):
        with patch(
            "slurm_quota.client.urlopen",
            return_value=_FakeUrlopenResponse({}, status=403),
        ):
            with self.assertRaises(ServiceHTTPError) as cm:
                APIClient(token="jwt").users_roles()
        self.assertEqual(cm.exception.status, 403)

    def test_users_roles_raises_value_error_when_token_missing(self):
        with self.assertRaises(ValueError):
            APIClient(token=None).users_roles()

    def test_grant_manager_sends_put(self):
        with patch(
            "slurm_quota.client.urlopen",
            return_value=_FakeUrlopenResponse({}, status=204),
        ) as m_urlopen:
            APIClient(token="jwt").grant_manager("bob")
        req = m_urlopen.call_args[0][0]
        self.assertEqual(req.method, "PUT")
        self.assertIn("/roles/managers/bob", req.full_url)

    def test_grant_manager_raises_service_http_error_on_bad_status(self):
        with patch(
            "slurm_quota.client.urlopen",
            return_value=_FakeUrlopenResponse({}, status=403),
        ):
            with self.assertRaises(ServiceHTTPError) as cm:
                APIClient(token="jwt").grant_manager("bob")
        self.assertEqual(cm.exception.status, 403)

    def test_grant_manager_raises_value_error_when_token_missing(self):
        with self.assertRaises(ValueError):
            APIClient(token=None).grant_manager("bob")

    def test_revoke_manager_sends_delete(self):
        with patch(
            "slurm_quota.client.urlopen",
            return_value=_FakeUrlopenResponse({}, status=204),
        ) as m_urlopen:
            APIClient(token="jwt").revoke_manager("bob")
        req = m_urlopen.call_args[0][0]
        self.assertEqual(req.method, "DELETE")
        self.assertIn("/roles/managers/bob", req.full_url)

    def test_revoke_manager_raises_service_http_error_on_bad_status(self):
        with patch(
            "slurm_quota.client.urlopen",
            return_value=_FakeUrlopenResponse({}, status=403),
        ):
            with self.assertRaises(ServiceHTTPError) as cm:
                APIClient(token="jwt").revoke_manager("bob")
        self.assertEqual(cm.exception.status, 403)

    def test_revoke_manager_raises_value_error_when_token_missing(self):
        with self.assertRaises(ValueError):
            APIClient(token=None).revoke_manager("bob")
