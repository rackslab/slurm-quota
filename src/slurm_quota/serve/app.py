# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Flask application for slurm-quota-serve."""

from __future__ import annotations

import logging
import ssl
import time
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request
from rfl.authentication.ldap import LDAPAuthentifier
from rfl.settings import RuntimeSettings
from rfl.web.tokens import RFLTokenizedWebApp
from werkzeug.exceptions import BadRequest, Forbidden, HTTPException, Unauthorized

from slurm_quota import auth
from slurm_quota.database import init_database
from slurm_quota.serve.settings import (
    ServeSetupError,
    load_bind_password,
    validate_settings,
)

logger = logging.getLogger("slurm_quota")


class SlurmQuotaServeApp(Flask, RFLTokenizedWebApp):
    def __init__(self) -> None:
        Flask.__init__(self, "slurm-quota-serve")
        self.settings: RuntimeSettings | None = None
        self.authentifier: LDAPAuthentifier | None = None
        self.ssl_context: ssl.SSLContext | None = None
        self._last_activity = time.monotonic()

    @property
    def scheme(self) -> str:
        return "HTTPS" if self.ssl_context is not None else "HTTP"

    def register(self) -> None:
        """Wire before-request hook, error handlers, and API routes."""
        from slurm_quota.serve import routes

        self.before_request(self.touch_activity)
        self.register_error_handler(401, self.unauthorized)
        self.register_error_handler(403, self.forbidden)
        self.register_error_handler(404, self.not_found)
        self.register_error_handler(400, self.bad_request)
        self.register_error_handler(HTTPException, self.http_exception)
        self.add_url_rule("/health", view_func=routes.health, methods=["GET"])
        self.add_url_rule("/login", view_func=routes.login, methods=["POST"])
        self.add_url_rule("/stats", view_func=routes.stats, methods=["GET"])
        self.add_url_rule("/me", view_func=routes.me, methods=["GET"])
        self.add_url_rule("/roles", view_func=routes.list_roles, methods=["GET"])
        self.add_url_rule(
            "/roles/operators/<username>",
            view_func=routes.grant_operator,
            methods=["PUT"],
        )
        self.add_url_rule(
            "/roles/operators/<username>",
            view_func=routes.revoke_operator,
            methods=["DELETE"],
        )
        self.add_url_rule(
            "/roles/managers/<username>",
            view_func=routes.grant_manager,
            methods=["PUT"],
        )
        self.add_url_rule(
            "/roles/managers/<username>",
            view_func=routes.revoke_manager,
            methods=["DELETE"],
        )
        self.add_url_rule(
            "/roles/managers/<username>/accounts",
            view_func=routes.list_manager_accounts_route,
            methods=["GET"],
        )
        self.add_url_rule(
            "/roles/managers/<username>/accounts/<account>",
            view_func=routes.grant_manager_account_route,
            methods=["PUT"],
        )
        self.add_url_rule(
            "/roles/managers/<username>/accounts/<account>",
            view_func=routes.revoke_manager_account_route,
            methods=["DELETE"],
        )
        self.add_url_rule(
            "/quotas/defaults",
            view_func=routes.get_default_quotas,
            methods=["GET"],
        )
        self.add_url_rule(
            "/quotas/defaults",
            view_func=routes.set_default_quotas,
            methods=["PUT"],
        )
        self.add_url_rule(
            "/quotas/<entity>/<name>/<resource>",
            view_func=routes.set_quota,
            methods=["PUT"],
        )
        self.add_url_rule(
            "/consumption/<entity>/<name>/<resource>",
            view_func=routes.adjust_consumption,
            methods=["PATCH"],
        )
        self.add_url_rule(
            "/factors/gpu",
            view_func=routes.get_gpu_factors,
            methods=["GET"],
        )
        self.add_url_rule(
            "/factors/gpu/<gpu_type>",
            view_func=routes.set_gpu_factor,
            methods=["PUT"],
        )

    def load_settings(self, conf_defs: Path, site_config: Path) -> None:
        self.settings = RuntimeSettings.yaml_definition(conf_defs)
        if site_config.exists():
            self.settings.override_ini(site_config)

    def dump(self) -> None:
        assert self.settings is not None
        self.settings.dump()

    def setup(self, conf_defs: Path, site_config: Path) -> None:
        self.load_settings(conf_defs, site_config)
        assert self.settings is not None
        validate_settings(self.settings)
        self.ssl_context = None
        if self.settings.tls.enabled:
            cert = self.settings.tls.cert
            key = self.settings.tls.key
            assert cert is not None and key is not None
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            try:
                context.load_cert_chain(certfile=str(cert), keyfile=str(key))
            except (OSError, ssl.SSLError) as exc:
                raise ServeSetupError(
                    f"Unable to load TLS certificate chain from {cert} and {key}: {exc}"
                ) from exc
            self.ssl_context = context
        self.authentifier = None
        RFLTokenizedWebApp.__init__(
            self,
            audience=self.settings.jwt.audience,
            algorithm=self.settings.jwt.algorithm,
            key=self.settings.jwt.key,
            create=self.settings.jwt.create,
            create_parent=self.settings.jwt.create_parent,
        )
        method = self.settings.authentication.method
        if method == "ldap":
            load_bind_password(self.settings)
            ldap = self.settings.ldap
            self.authentifier = LDAPAuthentifier(
                uri=ldap.uri,
                user_base=ldap.user_base,
                group_base=ldap.group_base,
                user_class=ldap.user_class,
                user_name_attribute=ldap.user_name_attribute,
                user_fullname_attribute=ldap.user_fullname_attribute,
                user_primary_group_attribute=ldap.user_primary_group_attribute,
                group_name_attribute=ldap.group_name_attribute,
                group_object_classes=ldap.group_object_classes,
                cacert=ldap.cacert,
                starttls=ldap.starttls,
                bind_dn=ldap.bind_dn,
                bind_password=ldap.bind_password,
                restricted_groups=ldap.restricted_groups,
                lookup_user_dn=ldap.lookup_user_dn,
            )
            logger.info("REST API authentication is enabled (LDAP + JWT)")
        elif method == "jwt":
            logger.info("REST API authentication is enabled (JWT)")
        try:
            auth.require_slurm_user()
        except PermissionError as exc:
            raise ServeSetupError(str(exc)) from exc
        init_database()

    def touch_activity(self) -> None:
        self._last_activity = time.monotonic()

    def _http_abort_response(
        self,
        exc: HTTPException,
        error: str,
        *,
        message: str | None = None,
    ) -> Any:
        """Log an HTTP abort and return a JSON error payload."""
        code = exc.code or 500
        description = exc.description or exc.name
        log_message = "%s %s: HTTP %s %s"
        args = (request.method, request.path, code, description)
        if code >= 500:
            logger.error(log_message, *args)
        else:
            logger.warning(log_message, *args)
        payload: dict[str, str] = {"error": error}
        if message is not None:
            payload["message"] = message
        return jsonify(payload), code

    def not_found(self, exc: HTTPException) -> Any:
        return self._http_abort_response(exc, "not_found")

    def unauthorized(self, exc: Unauthorized) -> Any:
        """Return JSON 401 for authentication failures and log the abort."""
        return self._http_abort_response(
            exc,
            "unauthorized",
            message=exc.description or "Authentication required",
        )

    def forbidden(self, exc: Forbidden) -> Any:
        """Return JSON 403 for permission denials and log the abort."""
        return self._http_abort_response(
            exc,
            "forbidden",
            message=exc.description or "Forbidden",
        )

    def bad_request(self, exc: BadRequest) -> Any:
        """Return JSON 400 for validation failures and log the abort."""
        return self._http_abort_response(
            exc,
            "bad_request",
            message=exc.description or "Bad request",
        )

    def http_exception(self, exc: HTTPException) -> Any:
        """Log and JSON-encode abort codes not handled by the 400/401/403/404 methods.

        Flask uses this only when no more specific handler matches (for example
        405 Method Not Allowed), so those responses are not silent HTML errors.
        """
        error = (exc.name or "error").lower().replace(" ", "_")
        return self._http_abort_response(
            exc,
            error,
            message=exc.description or exc.name,
        )
