"""Unit tests for slurm_quota.serve.token."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from slurm_quota.serve.settings import ServeSetupError
from slurm_quota.serve.token import issue_token, main
from tests.test_support import SlurmQuotaTestCase, serve_conf_defs
from tests.unit.serve.support import write_jwt_site_ini


class TestIssueToken(SlurmQuotaTestCase):
    def test_issues_token_for_jwt_method(self):
        with tempfile.TemporaryDirectory() as tmp:
            site_ini = write_jwt_site_ini(Path(tmp))
            token = issue_token(
                "alice",
                duration=None,
                conf_defs=serve_conf_defs(),
                site_config=site_ini,
            )
        self.assertIsInstance(token, str)
        self.assertTrue(token)

    def test_rejects_non_jwt_method(self):
        with tempfile.TemporaryDirectory() as tmp:
            site_ini = Path(tmp) / "serve.ini"
            jwt_key = Path(tmp) / "jwt.key"
            site_ini.write_text(
                f"""\
[authentication]
method=ldap

[ldap]
uri=ldap://localhost
user_base=ou=people,dc=example,dc=org
group_base=ou=groups,dc=example,dc=org

[jwt]
key={jwt_key}
create=yes
create_parent=yes
""",
                encoding="utf-8",
            )
            with self.assertRaises(ServeSetupError) as cm:
                issue_token(
                    "alice",
                    duration=None,
                    conf_defs=serve_conf_defs(),
                    site_config=site_ini,
                )
        self.assertIn("method=jwt", str(cm.exception))


class TestTokenCLIMain(SlurmQuotaTestCase):
    def test_rejects_non_root_user(self):
        with (
            patch(
                "slurm_quota.serve.token.auth.get_current_user", return_value="alice"
            ),
            patch.object(sys, "argv", ["slurm-quota-token", "alice"]),
            self.assertRaises(SystemExit) as cm,
        ):
            main()
        self.assertEqual(cm.exception.code, 1)
