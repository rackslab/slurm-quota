"""Helpers shared by unit and functional tests."""

from __future__ import annotations

import base64
import io
import json
from textwrap import dedent
from urllib.error import HTTPError

_HTTP_STATUS_REASONS = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    500: "Internal Server Error",
}


def fake_http_error(
    status: int,
    payload: dict | None = None,
    *,
    url: str = "http://127.0.0.1:9911/",
    body: bytes | None = None,
) -> HTTPError:
    """Build an HTTPError like urllib raises for failed HTTP responses."""
    if body is None:
        body = json.dumps(payload or {}).encode("utf-8")
    reason = _HTTP_STATUS_REASONS.get(status, "Error")
    return HTTPError(url, status, reason, None, io.BytesIO(body))


def dedent_lines(*lines: str) -> str:
    """
    Build multiline text for stdout/assert comparisons.

    Each logical line is prefixed with four spaces so ``textwrap.dedent`` can
    remove a common margin while preserving table alignment in the source file.
    """
    return dedent("\n".join(f"    {line}" for line in lines) + "\n")


def craft_jwt(*, login: str = "alice", exp: int = 4_102_444_800) -> str:
    """Build a minimal unsigned JWT string for CLI token inspection tests."""
    header = base64.urlsafe_b64encode(b"{}").decode().rstrip("=")
    payload = (
        base64.urlsafe_b64encode(json.dumps({"login": login, "exp": exp}).encode())
        .decode()
        .rstrip("=")
    )
    signature = base64.urlsafe_b64encode(b"fake").decode().rstrip("=")
    return f"{header}.{payload}.{signature}"
