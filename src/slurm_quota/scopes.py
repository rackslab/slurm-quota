# Copyright (c) 2025 Rackslab
# Copyright (c) 2025 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Token scopes restricting the REST API routes a token can access.

The scopes of a token are the scopes available for the current role of its
owner, restricted to the scopes carried by the token when it has a scopes
claim. Tokens obtained with POST /login or issued by slurm-quota-token have no
scopes claim.
"""

from __future__ import annotations

SCOPES: dict[str, str] = {
    "stats:read:own": "Read own resource statistics",
    "stats:read:managed": (
        "Read own resource statistics and statistics of managed accounts and their"
        " users"
    ),
    "stats:read:all": "Read resource statistics of all users and accounts",
    "quotas:read": "Read default quotas and GPU factors",
    "quotas:write": "Set quotas, default quotas, GPU factors and adjust consumption",
    "roles:read": "Read users roles and managers accounts",
    "roles:write": "Grant and revoke users roles and managers accounts",
}

SCOPE_ROLES: dict[str, frozenset[str]] = {
    "stats:read:own": frozenset({"admin", "operator", "manager", "user"}),
    "stats:read:managed": frozenset({"manager"}),
    "stats:read:all": frozenset({"admin", "operator"}),
    "quotas:read": frozenset({"admin", "operator"}),
    "quotas:write": frozenset({"admin", "operator"}),
    "roles:read": frozenset({"admin"}),
    "roles:write": frozenset({"admin"}),
}


# Stats scopes by increasing visibility.
STATS_SCOPES: tuple[str, ...] = (
    "stats:read:own",
    "stats:read:managed",
    "stats:read:all",
)


def scopes_for_role(role: str) -> list[str]:
    """Return the scopes available for this role."""
    return [scope for scope in SCOPES if role in SCOPE_ROLES[scope]]
