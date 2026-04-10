"""Unit tests for ``adjust_consumed_minutes``."""

from __future__ import annotations

from tests.test_support import SlurmQuotaTestCase


class TestAdjustConsumedMinutes(SlurmQuotaTestCase):
    def test_adjust_user_cpu_adds_minutes(self):
        self.init_db()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, total_consumed_cpu_minutes) VALUES (?, ?)",
                ("alice", 100),
            )
            conn.commit()

        new_value = self.sq.adjust_consumed_minutes("user", "alice", "cpu", 25)
        self.assertEqual(new_value, 125)

        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT total_consumed_cpu_minutes FROM users WHERE username = ?",
                ("alice",),
            ).fetchone()
        self.assertEqual(row, (125,))

    def test_adjust_account_gpu_clamps_to_zero(self):
        self.init_db()
        with self.db_connection() as conn:
            conn.execute(
                "INSERT INTO accounts (account, total_consumed_gpu_minutes) VALUES (?, ?)",
                ("hpc", 40),
            )
            conn.commit()

        new_value = self.sq.adjust_consumed_minutes("account", "hpc", "gpu", -100)
        self.assertEqual(new_value, 0)

        with self.db_connection() as conn:
            row = conn.execute(
                "SELECT total_consumed_gpu_minutes FROM accounts WHERE account = ?",
                ("hpc",),
            ).fetchone()
        self.assertEqual(row, (0,))

    def test_adjust_raises_for_missing_target(self):
        self.init_db()
        with self.assertRaisesRegex(ValueError, "User not found: missing"):
            self.sq.adjust_consumed_minutes("user", "missing", "cpu", 1)

    def test_adjust_rejects_invalid_target_type(self):
        with self.assertRaisesRegex(ValueError, "Invalid target type: team"):
            self.sq.adjust_consumed_minutes("team", "dev", "cpu", 1)

    def test_adjust_rejects_invalid_resource_type(self):
        with self.assertRaisesRegex(ValueError, "Invalid resource type: mem"):
            self.sq.adjust_consumed_minutes("user", "alice", "mem", 1)
