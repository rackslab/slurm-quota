# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Flask application for slurm-quota-serve."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Optional

from flask import Flask, jsonify
from werkzeug.exceptions import BadRequest, Forbidden, Unauthorized
from rfl.authentication.ldap import LDAPAuthentifier
from rfl.settings import RuntimeSettings
from rfl.web.tokens import RFLTokenizedWebApp

from slurm_quota import auth
from slurm_quota.database import init_database
from slurm_quota.serve.settings import (
    ServeSetupError,
    load_bind_password,
    validate_auth_settings,
)

logger = logging.getLogger("slurm_quota")


class SlurmQuotaServeApp(Flask, RFLTokenizedWebApp):
    def __init__(self) -> None:
        Flask.__init__(self, "slurm-quota-serve")
        self.settings: Optional[RuntimeSettings] = None
        self.authentifier: Optional[LDAPAuthentifier] = None
        self._last_activity = time.monotonic()

    def register(self) -> None:
        """Wire before-request hook, error handlers, and API routes."""
        from slurm_quota.serve import routes

        self.before_request(self.touch_activity)
        self.register_error_handler(401, self.unauthorized)
        self.register_error_handler(403, self.forbidden)
        self.register_error_handler(404, self.not_found)
        self.register_error_handler(400, self.bad_request)
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
        validate_auth_settings(self.settings)
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

    def not_found(self, _exc: Any) -> Any:
        return jsonify({"error": "not_found"}), 404

    def unauthorized(self, exc: Unauthorized) -> Any:
        return (
            jsonify(
                {
                    "error": "unauthorized",
                    "message": exc.description or "Authentication required",
                }
            ),
            401,
        )

    def forbidden(self, exc: Forbidden) -> Any:
        return (
            jsonify(
                {
                    "error": "forbidden",
                    "message": exc.description or "Forbidden",
                }
            ),
            403,
        )

    def bad_request(self, exc: BadRequest) -> Any:
        return (
            jsonify(
                {
                    "error": "bad_request",
                    "message": exc.description or "Bad request",
                }
            ),
            400,
        )
