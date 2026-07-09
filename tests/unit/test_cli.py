"""Unit tests for slurm_quota.cli."""

from __future__ import annotations

import argparse

from slurm_quota.cli import add_common_arguments, parse_signed_int
from tests.test_support import SlurmQuotaTestCase


class TestAddCommonArguments(SlurmQuotaTestCase):
    def test_registers_logging_flag_options(self):
        parser = argparse.ArgumentParser()
        add_common_arguments(parser)
        args = parser.parse_args(
            ["--debug", "--log-flags", "rfl", "--debug-flags", "werkzeug"]
        )

        self.assertTrue(args.debug)
        self.assertFalse(args.quiet)
        self.assertEqual(args.log_flags, ["rfl"])
        self.assertEqual(args.debug_flags, ["werkzeug"])


class TestParseSignedInt(SlurmQuotaTestCase):
    def test_accepts_positive_signed_value(self):
        self.assertEqual(parse_signed_int("+30"), 30)

    def test_accepts_negative_signed_value(self):
        self.assertEqual(parse_signed_int("-120"), -120)

    def test_rejects_unsigned_value(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_signed_int("30")

    def test_rejects_zero_without_sign(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_signed_int("0")

    def test_rejects_invalid_characters(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_signed_int("+3m")
