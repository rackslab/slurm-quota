"""Unit tests for slurm_quota.token.ClientToken."""

from __future__ import annotations

import stat
from datetime import datetime, timezone
from pathlib import Path

from slurm_quota.token import ClientToken
from tests.test_support import SlurmQuotaTestCase
from tests.testing_utils import craft_jwt


class TestClientTokenPath(SlurmQuotaTestCase):
    def test_path_uses_xdg_config_home(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env({"XDG_CONFIG_HOME": str(config_home)})
        self.assertEqual(
            ClientToken.path(),
            config_home / "slurm-quota" / "token",
        )


class TestClientTokenSave(SlurmQuotaTestCase):
    def test_save_creates_restricted_file(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env({"XDG_CONFIG_HOME": str(config_home)})
        path = ClientToken("eyJ.test.token\n", "").save()
        self.assertEqual(path.read_text(encoding="utf-8"), "eyJ.test.token")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(
            stat.S_IMODE(path.parent.stat().st_mode) & 0o777,
            0o700,
        )

    def test_load_env_only_then_save_writes_env_token(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env(
            {
                "XDG_CONFIG_HOME": str(config_home),
                "SLURM_QUOTA_TOKEN": "env-jwt-token",
            }
        )
        path = ClientToken.load(env_only=True).save()
        self.assertEqual(path.read_text(encoding="utf-8"), "env-jwt-token")

    def test_load_env_only_raises_when_env_missing(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env({"XDG_CONFIG_HOME": str(config_home)})
        with self.assertRaises(ValueError):
            ClientToken.load(env_only=True)


class TestClientTokenLoad(SlurmQuotaTestCase):
    def test_load_value_returns_token_string(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env({"XDG_CONFIG_HOME": str(config_home)})
        ClientToken("saved-token", "").save()
        self.assertEqual(ClientToken.load_value(), "saved-token")

    def test_load_value_returns_none_when_missing(self):
        config_home = Path(self._tmp.name) / "empty-config"
        self.env({"XDG_CONFIG_HOME": str(config_home)})
        self.assertIsNone(ClientToken.load_value())

    def test_load_reads_saved_file(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env({"XDG_CONFIG_HOME": str(config_home)})
        ClientToken("saved-token", "").save()
        loaded = ClientToken.load()
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.value, "saved-token")
        self.assertEqual(loaded.source, str(ClientToken.path()))

    def test_load_prefers_env_override(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env(
            {
                "XDG_CONFIG_HOME": str(config_home),
                "SLURM_QUOTA_TOKEN": "env-token",
            }
        )
        ClientToken("file-token", "").save()
        loaded = ClientToken.load()
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.value, "env-token")
        self.assertEqual(loaded.source, "SLURM_QUOTA_TOKEN (environment)")

    def test_load_returns_none_when_missing(self):
        config_home = Path(self._tmp.name) / "empty-config"
        self.env({"XDG_CONFIG_HOME": str(config_home)})
        self.assertIsNone(ClientToken.load())

    def test_load_ignores_empty_env_override(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env(
            {
                "XDG_CONFIG_HOME": str(config_home),
                "SLURM_QUOTA_TOKEN": "   ",
            }
        )
        ClientToken("file-token", "").save()
        loaded = ClientToken.load()
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.value, "file-token")
        self.assertEqual(loaded.source, str(ClientToken.path()))


class TestClientTokenDecode(SlurmQuotaTestCase):
    def test_decode_returns_token_payload(self):
        token = craft_jwt(login="alice", exp=4_102_444_800)
        client_token = ClientToken(token, "test")
        payload = client_token.decode()
        self.assertEqual(payload.claims["login"], "alice")
        self.assertEqual(payload.claims["exp"], 4_102_444_800)

    def test_decode_rejects_invalid_format(self):
        client_token = ClientToken("not-a-jwt", "test")
        with self.assertRaises(ValueError):
            client_token.decode()

    def test_decode_rejects_invalid_payload(self):
        client_token = ClientToken("a.b!!!.c", "test")
        with self.assertRaises(ValueError):
            client_token.decode()


class TestTokenPayloadUsername(SlurmQuotaTestCase):
    def test_username_returns_login_claim(self):
        token = craft_jwt(login="alice", exp=4_102_444_800)
        payload = ClientToken(token, "test").decode()
        self.assertEqual(payload.username(), "alice")

    def test_username_raises_when_missing(self):
        token = craft_jwt(login="", exp=4_102_444_800)
        payload = ClientToken(token, "test").decode()
        with self.assertRaises(ValueError):
            payload.username()


class TestTokenPayloadExpiry(SlurmQuotaTestCase):
    def test_expiry_marks_expired_tokens(self):
        past = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp())
        token = craft_jwt(login="alice", exp=past)
        payload = ClientToken(token, "test").decode()
        formatted = payload.expiry()
        self.assertIn("(expired)", formatted)

    def test_expiry_omits_expired_suffix_for_future_tokens(self):
        future = int(datetime(2099, 1, 1, tzinfo=timezone.utc).timestamp())
        token = craft_jwt(login="alice", exp=future)
        payload = ClientToken(token, "test").decode()
        formatted = payload.expiry()
        self.assertNotIn("(expired)", formatted)

    def test_expiry_raises_when_missing(self):
        header = "e30"
        payload_b64 = "e30"
        signature = "ZmFrZQ"
        payload = ClientToken(f"{header}.{payload_b64}.{signature}", "test").decode()
        with self.assertRaises(ValueError):
            payload.expiry()
