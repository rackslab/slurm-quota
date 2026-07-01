"""Functional tests: `stats` subcommand and global `--debug`."""

from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from slurm_quota.commands import RELOGIN_GUIDANCE
from slurm_quota.token import save_service_token, service_token_path
from tests.functional import functional_base
from tests.functional.functional_base import FunctionalCLIBase
from tests.testing_utils import dedent_lines, fake_http_error


class TestStatsCommand(FunctionalCLIBase):
    def _run_stats(
        self,
        argv: list[str],
        *,
        expected: str,
        current_user: str | None = None,
        payload_extend: dict | None = None,
    ) -> None:
        self.env({"NO_COLOR": "1"})
        payload = deepcopy(functional_base.stats_payload_full())
        if payload_extend:
            payload["users"].extend(deepcopy(payload_extend.get("users", [])))
            payload["accounts"].extend(deepcopy(payload_extend.get("accounts", [])))
        current_user_ctx = (
            patch("slurm_quota.auth.get_current_user", return_value=current_user)
            if current_user is not None
            else nullcontext()
        )
        with (
            patch.object(
                functional_base,
                "_STATS_REST_PAYLOAD",
                payload,
            ),
            patch(
                "slurm_quota.client.urlopen",
                side_effect=FunctionalCLIBase.stats_urlopen_side_effect,
            ),
            patch(
                "slurm_quota.commands.format_timestamp_with_timezone",
                return_value="TS_FIXED",
            ),
            current_user_ctx,
            self.capture_stdout() as out,
        ):
            self.run_cli_main(argv)
        self.assertEqual(out.getvalue(), expected)

    def test_stats_uses_current_user_when_username_omitted(self):
        self._run_stats(
            ["slurm-quota", "stats"],
            current_user="alice",
            expected=dedent_lines(
                "        |                                CPU                                 |                                GPU                                 |",
                "USER    |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "alice   |         120           60(2)      600 [██████░░░░░░░░░░░░░░]  30.0% |           0            0(2)        ∞                               | TS_FIXED                 ",
                "",
                "        |                                CPU                                 |                                GPU                                 |",
                "ACCOUNT |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "hpc     |         100            0(3)      500 [████░░░░░░░░░░░░░░░░]  20.0% |           0            0(3)        ∞                               | TS_FIXED                 ",
            ),
        )

    def test_stats_with_explicit_username_alice(self):
        self._run_stats(
            ["slurm-quota", "stats", "alice"],
            expected=dedent_lines(
                "        |                                CPU                                 |                                GPU                                 |",
                "USER    |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "alice   |         120           60(2)      600 [██████░░░░░░░░░░░░░░]  30.0% |           0            0(2)        ∞                               | TS_FIXED                 ",
                "",
                "        |                                CPU                                 |                                GPU                                 |",
                "ACCOUNT |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "hpc     |         100            0(3)      500 [████░░░░░░░░░░░░░░░░]  20.0% |           0            0(3)        ∞                               | TS_FIXED                 ",
            ),
        )

    def test_stats_with_user_option(self):
        self._run_stats(
            ["slurm-quota", "stats", "--user", "alice"],
            expected=dedent_lines(
                "        |                                CPU                                 |                                GPU                                 |",
                "USER    |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "alice   |         120           60(2)      600 [██████░░░░░░░░░░░░░░]  30.0% |           0            0(2)        ∞                               | TS_FIXED                 ",
                "",
                "        |                                CPU                                 |                                GPU                                 |",
                "ACCOUNT |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "hpc     |         100            0(3)      500 [████░░░░░░░░░░░░░░░░]  20.0% |           0            0(3)        ∞                               | TS_FIXED                 ",
            ),
        )

    def test_stats_with_explicit_username_bob(self):
        self._run_stats(
            ["slurm-quota", "stats", "bob"],
            expected=dedent_lines(
                "        |                                CPU                                 |                                GPU                                 |",
                "USER    |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "bob     |          30            0(1)        ∞                               |           0            0(1)        ∞                               | TS_FIXED                 ",
                "",
                "        |                                CPU                                 |                                GPU                                 |",
                "ACCOUNT |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "dev     |          40            0(0)        ∞                               |           0            0(0)        ∞                               | TS_FIXED                 ",
            ),
        )

    def test_stats_with_all(self):
        self._run_stats(
            ["slurm-quota", "stats", "--all"],
            expected=dedent_lines(
                "        |                                CPU                                 |                                GPU                                 |",
                "USER    |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "alice   |         120           60(2)      600 [██████░░░░░░░░░░░░░░]  30.0% |           0            0(2)        ∞                               | TS_FIXED                 ",
                "bob     |          30            0(1)        ∞                               |           0            0(1)        ∞                               | TS_FIXED                 ",
                "",
                "        |                                CPU                                 |                                GPU                                 |",
                "ACCOUNT |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "hpc     |         100            0(3)      500 [████░░░░░░░░░░░░░░░░]  20.0% |           0            0(3)        ∞                               | TS_FIXED                 ",
                "dev     |          40            0(0)        ∞                               |           0            0(0)        ∞                               | TS_FIXED                 ",
            ),
        )

    def test_stats_with_all_uses_longest_name_above_header_width(self):
        payload_extend = {
            "users": [
                {
                    "username": "research_member1",
                    "job_count": 0,
                    "last_updated": "2024-06-01T10:00:00",
                    "total_consumed_cpu_minutes": 1,
                    "total_preallocated_cpu_minutes": 0,
                    "quota_cpu_minutes": -1,
                    "total_consumed_gpu_minutes": 0,
                    "total_preallocated_gpu_minutes": 0,
                    "quota_gpu_minutes": -1,
                }
            ],
            "accounts": [
                {
                    "account": "platform_account",
                    "job_count": 0,
                    "last_updated": "2024-06-01T10:00:00",
                    "total_consumed_cpu_minutes": 1,
                    "total_preallocated_cpu_minutes": 0,
                    "quota_cpu_minutes": -1,
                    "total_consumed_gpu_minutes": 0,
                    "total_preallocated_gpu_minutes": 0,
                    "quota_gpu_minutes": -1,
                }
            ],
        }
        self._run_stats(
            ["slurm-quota", "stats", "--all"],
            payload_extend=payload_extend,
            expected=dedent_lines(
                "                 |                                CPU                                 |                                GPU                                 |",
                "USER             |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "-------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "alice            |         120           60(2)      600 [██████░░░░░░░░░░░░░░]  30.0% |           0            0(2)        ∞                               | TS_FIXED                 ",
                "bob              |          30            0(1)        ∞                               |           0            0(1)        ∞                               | TS_FIXED                 ",
                "research_member1 |           1            0(0)        ∞                               |           0            0(0)        ∞                               | TS_FIXED                 ",
                "",
                "                 |                                CPU                                 |                                GPU                                 |",
                "ACCOUNT          |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "-------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "hpc              |         100            0(3)      500 [████░░░░░░░░░░░░░░░░]  20.0% |           0            0(3)        ∞                               | TS_FIXED                 ",
                "dev              |          40            0(0)        ∞                               |           0            0(0)        ∞                               | TS_FIXED                 ",
                "platform_account |           1            0(0)        ∞                               |           0            0(0)        ∞                               | TS_FIXED                 ",
            ),
        )

    def test_stats_with_all_truncates_names_above_limit(self):
        payload_extend = {
            "users": [
                {
                    "username": "user_with_a_name_longer_than_thirty_chars",
                    "job_count": 0,
                    "last_updated": "2024-06-01T10:00:00",
                    "total_consumed_cpu_minutes": 1,
                    "total_preallocated_cpu_minutes": 0,
                    "quota_cpu_minutes": -1,
                    "total_consumed_gpu_minutes": 0,
                    "total_preallocated_gpu_minutes": 0,
                    "quota_gpu_minutes": -1,
                }
            ],
            "accounts": [
                {
                    "account": "account_with_a_name_longer_than_thirty_chars",
                    "job_count": 0,
                    "last_updated": "2024-06-01T10:00:00",
                    "total_consumed_cpu_minutes": 1,
                    "total_preallocated_cpu_minutes": 0,
                    "quota_cpu_minutes": -1,
                    "total_consumed_gpu_minutes": 0,
                    "total_preallocated_gpu_minutes": 0,
                    "quota_gpu_minutes": -1,
                }
            ],
        }
        self._run_stats(
            ["slurm-quota", "stats", "--all"],
            payload_extend=payload_extend,
            expected=dedent_lines(
                "                               |                                CPU                                 |                                GPU                                 |",
                "USER                           |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "alice                          |         120           60(2)      600 [██████░░░░░░░░░░░░░░]  30.0% |           0            0(2)        ∞                               | TS_FIXED                 ",
                "bob                            |          30            0(1)        ∞                               |           0            0(1)        ∞                               | TS_FIXED                 ",
                "user_with_a_name_longer_than_… |           1            0(0)        ∞                               |           0            0(0)        ∞                               | TS_FIXED                 ",
                "",
                "                               |                                CPU                                 |                                GPU                                 |",
                "ACCOUNT                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "hpc                            |         100            0(3)      500 [████░░░░░░░░░░░░░░░░]  20.0% |           0            0(3)        ∞                               | TS_FIXED                 ",
                "dev                            |          40            0(0)        ∞                               |           0            0(0)        ∞                               | TS_FIXED                 ",
                "account_with_a_name_longer_th… |           1            0(0)        ∞                               |           0            0(0)        ∞                               | TS_FIXED                 ",
            ),
        )

    def test_stats_with_account_option(self):
        self._run_stats(
            ["slurm-quota", "stats", "--account", "hpc"],
            expected=dedent_lines(
                "        |                                CPU                                 |                                GPU                                 |",
                "ACCOUNT |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "hpc     |         100            0(3)      500 [████░░░░░░░░░░░░░░░░]  20.0% |           0            0(3)        ∞                               | TS_FIXED                 ",
            ),
        )

    def test_stats_with_username_and_all(self):
        self._run_stats(
            ["slurm-quota", "stats", "alice", "--all"],
            expected=dedent_lines(
                "        |                                CPU                                 |                                GPU                                 |",
                "USER    |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "alice   |         120           60(2)      600 [██████░░░░░░░░░░░░░░]  30.0% |           0            0(2)        ∞                               | TS_FIXED                 ",
                "bob     |          30            0(1)        ∞                               |           0            0(1)        ∞                               | TS_FIXED                 ",
                "",
                "        |                                CPU                                 |                                GPU                                 |",
                "ACCOUNT |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "hpc     |         100            0(3)      500 [████░░░░░░░░░░░░░░░░]  20.0% |           0            0(3)        ∞                               | TS_FIXED                 ",
                "dev     |          40            0(0)        ∞                               |           0            0(0)        ∞                               | TS_FIXED                 ",
            ),
        )

    def test_stats_with_debug_and_all(self):
        self._run_stats(
            ["slurm-quota", "--debug", "stats", "--all"],
            expected=dedent_lines(
                "        |                                CPU                                 |                                GPU                                 |",
                "USER    |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "alice   |         120           60(2)      600 [██████░░░░░░░░░░░░░░]  30.0% |           0            0(2)        ∞                               | TS_FIXED                 ",
                "bob     |          30            0(1)        ∞                               |           0            0(1)        ∞                               | TS_FIXED                 ",
                "",
                "        |                                CPU                                 |                                GPU                                 |",
                "ACCOUNT |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "hpc     |         100            0(3)      500 [████░░░░░░░░░░░░░░░░]  20.0% |           0            0(3)        ∞                               | TS_FIXED                 ",
                "dev     |          40            0(0)        ∞                               |           0            0(0)        ∞                               | TS_FIXED                 ",
            ),
        )

    def test_stats_with_alice_and_hours(self):
        self._run_stats(
            ["slurm-quota", "stats", "alice", "--hours"],
            expected=dedent_lines(
                "        |                                CPU                                 |                                GPU                                 |",
                "USER    |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "alice   |        2.00         1.00(2)    10.00 [██████░░░░░░░░░░░░░░]  30.0% |        0.00         0.00(2)        ∞                               | TS_FIXED                 ",
                "",
                "        |                                CPU                                 |                                GPU                                 |",
                "ACCOUNT |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "hpc     |        1.67         0.00(3)     8.33 [████░░░░░░░░░░░░░░░░]  20.0% |        0.00         0.00(3)        ∞                               | TS_FIXED                 ",
            ),
        )

    def test_stats_with_all_and_hours(self):
        self._run_stats(
            ["slurm-quota", "stats", "--all", "--hours"],
            expected=dedent_lines(
                "        |                                CPU                                 |                                GPU                                 |",
                "USER    |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "alice   |        2.00         1.00(2)    10.00 [██████░░░░░░░░░░░░░░]  30.0% |        0.00         0.00(2)        ∞                               | TS_FIXED                 ",
                "bob     |        0.50         0.00(1)        ∞                               |        0.00         0.00(1)        ∞                               | TS_FIXED                 ",
                "",
                "        |                                CPU                                 |                                GPU                                 |",
                "ACCOUNT |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "hpc     |        1.67         0.00(3)     8.33 [████░░░░░░░░░░░░░░░░]  20.0% |        0.00         0.00(3)        ∞                               | TS_FIXED                 ",
                "dev     |        0.67         0.00(0)        ∞                               |        0.00         0.00(0)        ∞                               | TS_FIXED                 ",
            ),
        )

    def test_stats_rejects_positional_and_user_option(self):
        self.run_cli_main_exit(["slurm-quota", "stats", "alice", "--user", "bob"], 2)

    def test_stats_rejects_user_and_account_options(self):
        self.run_cli_main_exit(
            ["slurm-quota", "stats", "--user", "alice", "--account", "hpc"], 2
        )

    def test_stats_rejects_positional_and_account_options(self):
        self.run_cli_main_exit(["slurm-quota", "stats", "alice", "--account", "hpc"], 2)

    def test_stats_sends_bearer_when_token_file_exists(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env({"NO_COLOR": "1", "XDG_CONFIG_HOME": str(config_home)})
        token_path = service_token_path()
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text("saved-jwt", encoding="utf-8")
        with (
            patch.object(
                functional_base,
                "_STATS_REST_PAYLOAD",
                deepcopy(functional_base.stats_payload_full()),
            ),
            patch(
                "slurm_quota.client.urlopen",
                side_effect=FunctionalCLIBase.stats_urlopen_side_effect,
            ) as m_urlopen,
            patch(
                "slurm_quota.commands.format_timestamp_with_timezone",
                return_value="TS_FIXED",
            ),
            self.capture_stdout(),
        ):
            self.run_cli_main(["slurm-quota", "stats", "alice"])
        req = m_urlopen.call_args[0][0]
        self.assertEqual(req.get_header("Authorization"), "Bearer saved-jwt")

    def test_stats_expired_token_via_http_error(self):
        config_home = Path(self._tmp.name) / "xdg-config"
        self.env({"NO_COLOR": "1", "XDG_CONFIG_HOME": str(config_home)})
        save_service_token("saved-jwt")

        def _expired(_request, **_kwargs):
            raise fake_http_error(
                401,
                {
                    "error": "unauthorized",
                    "message": "Token is expired",
                },
            )

        with (
            patch("slurm_quota.client.urlopen", side_effect=_expired),
            self.assertLogs("slurm_quota", level="INFO") as log_cm,
        ):
            self.run_cli_main_exit(["slurm-quota", "stats", "alice"], 1)
        self.assertIn("expired", log_cm.output[0].lower())
        self.assertIn(RELOGIN_GUIDANCE, log_cm.output[1])
