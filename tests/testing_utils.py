"""Helpers shared by unit and functional tests."""

from __future__ import annotations

from textwrap import dedent


def dedent_lines(*lines: str) -> str:
    """
    Build multiline text for stdout/assert comparisons.

    Each logical line is prefixed with four spaces so ``textwrap.dedent`` can
    remove a common margin while preserving table alignment in the source file.
    """
    return dedent("\n".join(f"    {line}" for line in lines) + "\n")
