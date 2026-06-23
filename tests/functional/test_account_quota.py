"""Functional tests: `account-quota` subcommand."""

from __future__ import annotations

import json
from unittest.mock import patch

from tests.functional.functional_base import (
    FakeNoContentUrlopenResponse,
    FunctionalAPICliBase,
)


class TestAccountQuotaCommand(FunctionalAPICliBase):
    def test_account_quota_calls_api(self):
        with (
            patch(
                "slurm_quota.client.urlopen",
                return_value=FakeNoContentUrlopenResponse(status=204),
            ) as m_urlopen,
            self.capture_stdout() as out,
        ):
            self.run_cli_main(["slurm-quota", "account-quota", "astrophysics", "-1"])
        self.assertEqual(
            out.getvalue(),
            "Successfully set quota for account astrophysics: -1 CPU minutes\n",
        )
        req = m_urlopen.call_args[0][0]
        self.assertEqual(req.get_method(), "PUT")
        self.assertTrue(req.full_url.endswith("/quotas/accounts/astrophysics/cpu"))
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body, {"quota_minutes": -1})
