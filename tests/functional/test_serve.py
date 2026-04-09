"""Functional tests: `serve` subcommand (`--host`, `--port`, `--idle-timeout`)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tests.functional.functional_base import FunctionalCLIBase


class TestServeCommand(FunctionalCLIBase):
    def test_serve_passes_host_port_idle_timeout(self):
        self.init_db()
        mock_run = MagicMock()
        with patch.object(self.sq, "run_serve_command", mock_run):
            self.run_main(
                [
                    "slurm-quota",
                    "serve",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "12345",
                    "--idle-timeout",
                    "42",
                ]
            )
        mock_run.assert_called_once_with("0.0.0.0", 12345, 42)
