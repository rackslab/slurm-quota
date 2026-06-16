"""Functional tests: `stats` subcommand and global `--debug`."""

from __future__ import annotations

from copy import deepcopy
from contextlib import nullcontext
from typing import Optional
from unittest.mock import patch

from tests.functional import functional_base
from tests.functional.functional_base import FunctionalCLIBase
from tests.testing_utils import dedent_lines


class TestStatsCommand(FunctionalCLIBase):
    def _run_stats(
        self,
        argv: list[str],
        *,
        expected: str,
        current_user: Optional[str] = None,
        payload_extend: Optional[dict] = None,
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
