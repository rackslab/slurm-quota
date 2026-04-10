"""Unit tests for ``parse_signed_int``."""

from __future__ import annotations

import argparse

from tests.test_support import SlurmQuotaTestCase


class TestParseSignedInt(SlurmQuotaTestCase):
    def test_accepts_positive_signed_value(self):
        self.assertEqual(self.sq.parse_signed_int("+30"), 30)

    def test_accepts_negative_signed_value(self):
        self.assertEqual(self.sq.parse_signed_int("-120"), -120)

    def test_rejects_unsigned_value(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            self.sq.parse_signed_int("30")

    def test_rejects_zero_without_sign(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            self.sq.parse_signed_int("0")

    def test_rejects_invalid_characters(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            self.sq.parse_signed_int("+3m")
