"""Unit tests for slurm_quota.log."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from slurm_quota.log import (
    DEFAULT_DEBUG_FLAGS,
    DEFAULT_LOG_FLAGS,
    setup_logging,
)
from tests.test_support import SlurmQuotaTestCase


def _args(**kwargs):
    defaults = {
        "debug": False,
        "quiet": False,
        "log_flags": None,
        "debug_flags": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestSetupLogging(SlurmQuotaTestCase):
    @patch("slurm_quota.log.setup_logger")
    def test_defaults(self, mock_setup_logger):
        setup_logging(_args())

        mock_setup_logger.assert_called_once_with(
            debug=False,
            level=None,
            log_flags=DEFAULT_LOG_FLAGS,
            debug_flags=[],
        )

    @patch("slurm_quota.log.setup_logger")
    def test_debug_mode(self, mock_setup_logger):
        setup_logging(_args(debug=True))

        mock_setup_logger.assert_called_once_with(
            debug=True,
            level=None,
            log_flags=DEFAULT_LOG_FLAGS,
            debug_flags=DEFAULT_DEBUG_FLAGS,
        )

    @patch("slurm_quota.log.setup_logger")
    def test_quiet_mode(self, mock_setup_logger):
        setup_logging(_args(quiet=True))

        mock_setup_logger.assert_called_once_with(
            debug=False,
            level="WARNING",
            log_flags=DEFAULT_LOG_FLAGS,
            debug_flags=[],
        )

    @patch("slurm_quota.log.setup_logger")
    def test_custom_flags(self, mock_setup_logger):
        setup_logging(
            _args(
                debug=True,
                log_flags=["rfl", "slurm_quota"],
                debug_flags=["rfl"],
            )
        )

        mock_setup_logger.assert_called_once_with(
            debug=True,
            level=None,
            log_flags=["rfl", "slurm_quota"],
            debug_flags=["rfl"],
        )

    @patch("slurm_quota.log.setup_logger")
    def test_cli_overrides_config(self, mock_setup_logger):
        setup_logging(
            _args(
                debug=True,
                log_flags=["rfl"],
                debug_flags=["werkzeug"],
            ),
            config_log_flags=["slurm_quota"],
            config_debug_flags=["slurm_quota"],
        )

        mock_setup_logger.assert_called_once_with(
            debug=True,
            level=None,
            log_flags=["rfl"],
            debug_flags=["werkzeug"],
        )

    @patch("slurm_quota.log.setup_logger")
    def test_config_used_when_cli_unset(self, mock_setup_logger):
        setup_logging(
            _args(),
            config_log_flags=["rfl"],
            config_debug_flags=["rfl"],
        )

        mock_setup_logger.assert_called_once_with(
            debug=False,
            level=None,
            log_flags=["rfl"],
            debug_flags=["rfl"],
        )

    @patch("slurm_quota.log.setup_logger")
    def test_defaults_when_cli_and_config_unset(self, mock_setup_logger):
        setup_logging(_args())

        mock_setup_logger.assert_called_once_with(
            debug=False,
            level=None,
            log_flags=DEFAULT_LOG_FLAGS,
            debug_flags=[],
        )
