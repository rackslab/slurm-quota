# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Flask application for the slurm-quota web dashboard."""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from http import HTTPStatus
from typing import Any, Optional

from flask import Flask, abort, redirect, render_template, request
from werkzeug.exceptions import HTTPException

from slurm_quota.web import routes
from slurm_quota.web.auth import login_url, session_token
from slurm_quota.token import load_service_token
from slurm_quota.web.settings import assets_root, load_session_key

logger = logging.getLogger("slurm_quota")


class SlurmQuotaWebApp(Flask):
    def __init__(self) -> None:
        web_root = assets_root()
        super().__init__(
            "slurm-quota-web",
            template_folder=str(web_root / "templates"),
            static_folder=str(web_root / "static"),
        )
        session_key = load_session_key()
        if session_key:
            self.secret_key = session_key
        self.config["SESSION_COOKIE_HTTPONLY"] = True
        self.config["SESSION_COOKIE_SECURE"] = os.environ.get(
            "SLURM_QUOTA_WEB_SECURE_COOKIES", ""
        ).lower() in {"1", "true", "yes", "on"}
        self.config["SESSION_COOKIE_SAMESITE"] = "Lax"
        days = int(os.environ.get("SLURM_QUOTA_WEB_SESSION_DAYS", "1"))
        self.permanent_session_lifetime = timedelta(days=max(days, 1))

    def ensure_session_key(self) -> None:
        if self.secret_key:
            return
        description = (
            "SLURM_QUOTA_WEB_SESSION_KEY or SLURM_QUOTA_WEB_SESSION_KEY_FILE must be "
            "set for browser login sessions"
        )
        logger.error(description)
        abort(HTTPStatus.SERVICE_UNAVAILABLE, description=description)

    def register(self) -> None:
        """Wire before-request hook and dashboard routes."""
        self.register_error_handler(
            HTTPStatus.SERVICE_UNAVAILABLE, self.service_unavailable
        )
        self.before_request(self.require_login)
        self.add_url_rule("/login", view_func=routes.login, methods=["GET"])
        self.add_url_rule("/login", view_func=routes.login_post, methods=["POST"])
        self.add_url_rule("/logout", view_func=routes.logout, methods=["POST"])
        self.add_url_rule("/", view_func=routes.dashboard, methods=["GET"])

    def service_unavailable(self, error: HTTPException) -> tuple[str, int]:
        return render_template("error.html"), error.code

    def require_login(self) -> Optional[Any]:
        if request.endpoint in {"static", "login", "login_post", "logout"}:
            return None

        if load_service_token() is not None:
            return None

        self.ensure_session_key()
        if session_token() is not None:
            return None

        return redirect(login_url())
