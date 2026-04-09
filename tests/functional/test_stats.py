"""Functional tests: `stats` subcommand and global `--debug`."""

from __future__ import annotations

from typing import Optional
from unittest.mock import patch

from tests.functional.functional_base import FunctionalCLIBase
from tests.testing_utils import dedent_lines


class TestStatsCommand(FunctionalCLIBase):
    def _run_stats(
        self,
        argv: list[str],
        *,
        expected: str,
        current_user: Optional[str] = None,
    ) -> None:
        self.env({"NO_COLOR": "1"})
        u = patch.object(
            self.sq,
            "urlopen",
            side_effect=FunctionalCLIBase.stats_urlopen_side_effect,
        )
        t = patch.object(
            self.sq, "format_timestamp_with_timezone", return_value="TS_FIXED"
        )
        if current_user is not None:
            with (
                u,
                t,
                patch.object(self.sq, "get_current_user", return_value=current_user),
            ):
                with self.capture_stdout() as out:
                    self.run_main(argv)
        else:
            with u, t:
                with self.capture_stdout() as out:
                    self.run_main(argv)
        self.assertEqual(out.getvalue(), expected)

    def test_stats_uses_current_user_when_username_omitted(self):
        self._run_stats(
            ["slurm-quota", "stats"],
            current_user="alice",
            expected=dedent_lines(
                "                          |                                CPU                                 |                                GPU                                 |",
                "USER                      |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "alice                     |         120           60(2)      600 [██████░░░░░░░░░░░░░░]  30.0% |           0            0(2)        ∞                               | TS_FIXED                 ",
                "",
                "                          |                                CPU                                 |                                GPU                                 |",
                "ACCOUNT                   |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "hpc                       |         100            0(3)      500 [████░░░░░░░░░░░░░░░░]  20.0% |           0            0(3)        ∞                               | TS_FIXED                 ",
            ),
        )

    def test_stats_with_explicit_username_alice(self):
        self._run_stats(
            ["slurm-quota", "stats", "alice"],
            expected=dedent_lines(
                "                          |                                CPU                                 |                                GPU                                 |",
                "USER                      |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "alice                     |         120           60(2)      600 [██████░░░░░░░░░░░░░░]  30.0% |           0            0(2)        ∞                               | TS_FIXED                 ",
                "",
                "                          |                                CPU                                 |                                GPU                                 |",
                "ACCOUNT                   |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "hpc                       |         100            0(3)      500 [████░░░░░░░░░░░░░░░░]  20.0% |           0            0(3)        ∞                               | TS_FIXED                 ",
            ),
        )

    def test_stats_with_explicit_username_bob(self):
        self._run_stats(
            ["slurm-quota", "stats", "bob"],
            expected=dedent_lines(
                "                          |                                CPU                                 |                                GPU                                 |",
                "USER                      |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "bob                       |          30            0(1)        ∞                               |           0            0(1)        ∞                               | TS_FIXED                 ",
                "",
                "                          |                                CPU                                 |                                GPU                                 |",
                "ACCOUNT                   |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "dev                       |          40            0(0)        ∞                               |           0            0(0)        ∞                               | TS_FIXED                 ",
            ),
        )

    def test_stats_with_all(self):
        self._run_stats(
            ["slurm-quota", "stats", "--all"],
            expected=dedent_lines(
                "                          |                                CPU                                 |                                GPU                                 |",
                "USER                      |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "alice                     |         120           60(2)      600 [██████░░░░░░░░░░░░░░]  30.0% |           0            0(2)        ∞                               | TS_FIXED                 ",
                "bob                       |          30            0(1)        ∞                               |           0            0(1)        ∞                               | TS_FIXED                 ",
                "",
                "                          |                                CPU                                 |                                GPU                                 |",
                "ACCOUNT                   |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "hpc                       |         100            0(3)      500 [████░░░░░░░░░░░░░░░░]  20.0% |           0            0(3)        ∞                               | TS_FIXED                 ",
                "dev                       |          40            0(0)        ∞                               |           0            0(0)        ∞                               | TS_FIXED                 ",
            ),
        )

    def test_stats_with_username_and_all(self):
        self._run_stats(
            ["slurm-quota", "stats", "alice", "--all"],
            expected=dedent_lines(
                "                          |                                CPU                                 |                                GPU                                 |",
                "USER                      |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "alice                     |         120           60(2)      600 [██████░░░░░░░░░░░░░░]  30.0% |           0            0(2)        ∞                               | TS_FIXED                 ",
                "bob                       |          30            0(1)        ∞                               |           0            0(1)        ∞                               | TS_FIXED                 ",
                "",
                "                          |                                CPU                                 |                                GPU                                 |",
                "ACCOUNT                   |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "hpc                       |         100            0(3)      500 [████░░░░░░░░░░░░░░░░]  20.0% |           0            0(3)        ∞                               | TS_FIXED                 ",
                "dev                       |          40            0(0)        ∞                               |           0            0(0)        ∞                               | TS_FIXED                 ",
            ),
        )

    def test_stats_with_debug_and_all(self):
        self._run_stats(
            ["slurm-quota", "--debug", "stats", "--all"],
            expected=dedent_lines(
                "                          |                                CPU                                 |                                GPU                                 |",
                "USER                      |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "alice                     |         120           60(2)      600 [██████░░░░░░░░░░░░░░]  30.0% |           0            0(2)        ∞                               | TS_FIXED                 ",
                "bob                       |          30            0(1)        ∞                               |           0            0(1)        ∞                               | TS_FIXED                 ",
                "",
                "                          |                                CPU                                 |                                GPU                                 |",
                "ACCOUNT                   |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "hpc                       |         100            0(3)      500 [████░░░░░░░░░░░░░░░░]  20.0% |           0            0(3)        ∞                               | TS_FIXED                 ",
                "dev                       |          40            0(0)        ∞                               |           0            0(0)        ∞                               | TS_FIXED                 ",
            ),
        )

    def test_stats_with_alice_and_hours(self):
        self._run_stats(
            ["slurm-quota", "stats", "alice", "--hours"],
            expected=dedent_lines(
                "                          |                                CPU                                 |                                GPU                                 |",
                "USER                      |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "alice                     |        2.00         1.00(2)    10.00 [██████░░░░░░░░░░░░░░]  30.0% |        0.00         0.00(2)        ∞                               | TS_FIXED                 ",
                "",
                "                          |                                CPU                                 |                                GPU                                 |",
                "ACCOUNT                   |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "hpc                       |        1.67         0.00(3)     8.33 [████░░░░░░░░░░░░░░░░]  20.0% |        0.00         0.00(3)        ∞                               | TS_FIXED                 ",
            ),
        )

    def test_stats_with_all_and_hours(self):
        self._run_stats(
            ["slurm-quota", "stats", "--all", "--hours"],
            expected=dedent_lines(
                "                          |                                CPU                                 |                                GPU                                 |",
                "USER                      |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "alice                     |        2.00         1.00(2)    10.00 [██████░░░░░░░░░░░░░░]  30.0% |        0.00         0.00(2)        ∞                               | TS_FIXED                 ",
                "bob                       |        0.50         0.00(1)        ∞                               |        0.00         0.00(1)        ∞                               | TS_FIXED                 ",
                "",
                "                          |                                CPU                                 |                                GPU                                 |",
                "ACCOUNT                   |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        |    CONSUMED  PREALLOC(JOBS)    QUOTA STATUS                        | LAST UPDATED             ",
                "----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
                "hpc                       |        1.67         0.00(3)     8.33 [████░░░░░░░░░░░░░░░░]  20.0% |        0.00         0.00(3)        ∞                               | TS_FIXED                 ",
                "dev                       |        0.67         0.00(0)        ∞                               |        0.00         0.00(0)        ∞                               | TS_FIXED                 ",
            ),
        )
