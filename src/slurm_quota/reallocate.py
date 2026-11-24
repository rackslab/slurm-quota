# Copyright (c) 2026 Rackslab
# Copyright (c) 2026 Université de Montpellier
# SPDX-License-Identifier: GPL-2.0-or-later

"""Reallocation of unattributed legacy consumption to user/account pairs.

The migration to per user/account pair consumption stores the legacy
consumption of each user on pair (user, UNATTRIBUTED) and the legacy consumption
of each account on pair (UNATTRIBUTED, account). This module uses Slurm
accounting to determine actual user/account pairs and their shares, distributes
unattributed consumption accordingly and removes all unattributed pairs, user
and account.
"""

import argparse
import datetime
import math
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

import slurm_quota
from slurm_quota import auth, slurm as slurm_integration
from slurm_quota.cli import add_common_arguments
from slurm_quota.database import (
    connect_database,
    get_default_quota_settings,
    load_gpu_factors,
)
from slurm_quota.log import logger, setup_logging
from slurm_quota.migrate import UNATTRIBUTED

Pair = tuple[str, str]

# Sources of user/account pairs, by order of preference.
SOURCE_SACCT = "sacct"
SOURCE_SACCT_OTHER = "sacct-other-resource"
SOURCE_ASSOCIATIONS = "associations"
SOURCES = (SOURCE_SACCT, SOURCE_SACCT_OTHER, SOURCE_ASSOCIATIONS)
# Users and accounts without any pair from the sources above.
SOURCE_NONE = "none"

UNPAIRED_REASON = (
    "no job in Slurm accounting in the selected period nor Slurm association "
    "found, its consumption is left unattributed"
)

IPF_MAX_ITERATIONS = 1000
IPF_TOLERANCE = 1e-9

# Differences between reallocated and unattributed consumption of accounts
# within this ratio of their unattributed consumption are due to the rounding of
# the distribution, they are reported together in a single warning.
NEGLIGIBLE_RATIO = 0.001
# Maximum number of users listed in warnings.
MAX_LISTED_USERS = 5

# Environment variable to select another database than the production one.
DB_PATH_ENV = "SLURM_QUOTA_DB"


class ReallocationError(Exception):
    """Raised when unattributed consumption cannot be reallocated."""


def _apportion(numerators: list[int], denominator: int, total: int) -> list[int]:
    """Round numerators / denominator to integers summing to total.

    Largest remainder method on exact integer arithmetic: every value is floored,
    then the units still missing to reach total are given to the values with the
    largest remainders (ties broken by position, for determinism). The caller
    must ensure sum(numerators) == total * denominator.
    """
    floors = [n // denominator for n in numerators]
    remainders = [n % denominator for n in numerators]
    missing = total - sum(floors)
    order = sorted(range(len(numerators)), key=lambda i: (-remainders[i], i))
    for i in order[:missing]:
        floors[i] += 1
    return floors


def _round_to_total(values: list[float], total: int) -> list[int]:
    """Round non-negative values to integers summing to total.

    Largest remainder method on floating point values whose sum is close to
    total. Units are added to the values with the largest fractional parts, or
    removed from the values with the smallest ones (never below zero), ties
    broken by position.
    """
    floors = [math.floor(value) for value in values]
    fractions = [value - floor for value, floor in zip(values, floors)]
    missing = total - sum(floors)
    if missing > 0:
        order = sorted(range(len(values)), key=lambda i: (-fractions[i], i))
        for step in range(missing):
            floors[order[step % len(order)]] += 1
    elif missing < 0:
        order = sorted(range(len(values)), key=lambda i: (fractions[i], i))
        for i in order:
            if missing == 0:
                break
            if floors[i] > 0:
                floors[i] -= 1
                missing += 1
    return floors


def _account_targets(
    account_totals: dict[str, int], total_users: int
) -> dict[str, int]:
    """Return account targets rescaled to the overall users total if needed."""
    total_accounts = sum(account_totals.values())
    if total_accounts == total_users:
        return dict(account_totals)
    accounts = sorted(account_totals)
    return dict(
        zip(
            accounts,
            _apportion(
                [account_totals[name] * total_users for name in accounts],
                total_accounts,
                total_users,
            ),
        )
    )


@dataclass
class FitResult:
    """Result of fit_consumption().

    Attributes:
        pairs: Consumption per (user, account) pair, only positive values.
        account_targets: Account totals actually targeted, rescaled to the
            overall user total when overall user and account totals differ.
            Empty when account totals are not constrained.
        unreachable: Accounts whose total cannot reach its target with the
            given pairs, mapped to the remaining difference (positive when the
            account gets more than its target).
    """

    pairs: dict[Pair, int] = field(default_factory=dict)
    account_targets: dict[str, int] = field(default_factory=dict)
    unreachable: dict[str, int] = field(default_factory=dict)


def fit_consumption(
    user_totals: dict[str, int],
    account_totals: dict[str, int],
    weights: dict[Pair, float],
) -> FitResult:
    """Distribute user and account totals among weighted user/account pairs.

    Pairs and their weights define the support and the initial shares of the
    distribution. Iterative proportional fitting scales the weights so that pair
    values sum to user totals and account totals, then values are rounded to
    integers. User totals are always preserved exactly. Account totals are
    preserved exactly when overall user and account totals are equal and the
    support allows it. Otherwise, account totals are rescaled to the overall
    user total, and accounts whose rescaled total cannot be reached with the
    given pairs are reported.

    When only one side has non-zero totals, only this side is constrained and
    the other side receives what the weights give.

    Args:
        user_totals: Consumption per username.
        account_totals: Consumption per account (same unit).
        weights: Positive weight per (user, account) pair. Every user and
            account with a positive total must be part of at least one pair.

    Returns:
        The fitted consumption per pair, the account targets and the accounts
        whose target is unreachable.

    Raises:
        ReallocationError: If a user or account with a positive total has no
            pair.
    """
    users = sorted(name for name, value in user_totals.items() if value > 0)
    accounts = sorted(name for name, value in account_totals.items() if value > 0)
    if not users and not accounts:
        return FitResult()

    edges = sorted(pair for pair, weight in weights.items() if weight > 0)
    row_edges: dict[str, list[Pair]] = {}
    col_edges: dict[str, list[Pair]] = {}
    for pair in edges:
        row_edges.setdefault(pair[0], []).append(pair)
        col_edges.setdefault(pair[1], []).append(pair)
    missing = [f"user {name}" for name in users if name not in row_edges] + [
        f"account {name}" for name in accounts if name not in col_edges
    ]
    if missing:
        raise ReallocationError(f"No user/account pair for {', '.join(missing)}")

    total_users = sum(user_totals[name] for name in users)
    rows: dict[str, int] = {name: user_totals[name] for name in users}
    cols: dict[str, int] = {}
    if users and accounts:
        cols = _account_targets(
            {name: account_totals[name] for name in accounts}, total_users
        )
    elif accounts:
        cols = {name: account_totals[name] for name in accounts}

    # Keep only edges of constrained sides.
    if rows:
        edges = [pair for pair in edges if pair[0] in rows]
    if cols:
        edges = [pair for pair in edges if pair[1] in cols]
    row_edges = {}
    col_edges = {}
    for pair in edges:
        row_edges.setdefault(pair[0], []).append(pair)
        col_edges.setdefault(pair[1], []).append(pair)
    values = {pair: float(weights[pair]) for pair in edges}

    def scale(groups: dict[str, list[Pair]], targets: dict[str, int]) -> None:
        for name, target in targets.items():
            group = groups.get(name, [])
            current = sum(values[pair] for pair in group)
            if current > 0:
                factor = target / current
                for pair in group:
                    values[pair] *= factor

    if rows and cols:
        for _ in range(IPF_MAX_ITERATIONS):
            scale(col_edges, cols)
            scale(row_edges, rows)
            error = max(
                abs(sum(values[pair] for pair in col_edges[name]) - target)
                / max(target, 1)
                for name, target in cols.items()
            )
            if error < IPF_TOLERANCE:
                break
    elif rows:
        scale(row_edges, rows)
    else:
        scale(col_edges, cols)

    # Round to integers, preserving the totals of the main constrained side.
    cells: dict[Pair, int] = {}
    if rows:
        for name, target in rows.items():
            group = row_edges[name]
            for pair, value in zip(
                group, _round_to_total([values[pair] for pair in group], target)
            ):
                cells[pair] = value
    else:
        for name, target in cols.items():
            group = col_edges[name]
            for pair, value in zip(
                group, _round_to_total([values[pair] for pair in group], target)
            ):
                cells[pair] = value

    unreachable: dict[str, int] = {}
    if rows and cols:
        unreachable = _fix_columns(cells, values, row_edges, cols)
    return FitResult(
        pairs={pair: value for pair, value in cells.items() if value > 0},
        account_targets=cols,
        unreachable=unreachable,
    )


def _fix_columns(
    cells: dict[Pair, int],
    values: dict[Pair, float],
    row_edges: dict[str, list[Pair]],
    targets: dict[str, int],
) -> dict[str, int]:
    """Move units within user rows to match account targets after rounding.

    Units are moved from accounts above their target to accounts below it,
    within the same user row and only between pairs of the support, so user
    totals are preserved and cells stay non-negative. Rows whose rounding of
    the source pair was the least favourable are preferred.

    Returns:
        Mapping of accounts to their remaining deviation from target, for
        accounts that cannot be fixed within the support.
    """
    deviations = {name: -target for name, target in targets.items()}
    for (_, account), value in cells.items():
        deviations[account] += value

    def fraction(pair: Pair) -> float:
        return values[pair] - math.floor(values[pair])

    progress = True
    while progress:
        progress = False
        surplus = sorted(name for name, dev in deviations.items() if dev > 0)
        deficit = sorted(name for name, dev in deviations.items() if dev < 0)
        for src in surplus:
            for dst in deficit:
                if deviations[src] <= 0 or deviations[dst] >= 0:
                    continue
                candidates = sorted(
                    (
                        user
                        for user, group in row_edges.items()
                        if cells.get((user, src), 0) > 0 and (user, dst) in values
                    ),
                    key=lambda user: (
                        fraction((user, src)) - fraction((user, dst)),
                        user,
                    ),
                )
                for user in candidates:
                    moves = min(cells[(user, src)], deviations[src], -deviations[dst])
                    if moves <= 0:
                        break
                    cells[(user, src)] -= moves
                    cells[(user, dst)] += moves
                    deviations[src] -= moves
                    deviations[dst] += moves
                    progress = True

    # Remaining deviations involve accounts without common users. Move units
    # along chains of accounts linked by common users: from a surplus account
    # to an intermediate account through one user, then from the intermediate
    # account to the next through another user, until a deficit account.
    while any(dev > 0 for dev in deviations.values()) and any(
        dev < 0 for dev in deviations.values()
    ):
        path = _find_moves_path(cells, values, row_edges, deviations)
        if path is None:
            break  # Not feasible within the support
        moves = min(
            deviations[path[0][1]],
            -deviations[path[-1][2]],
            min(cells[(user, src)] for user, src, _ in path),
        )
        for user, src, dst in path:
            cells[(user, src)] -= moves
            cells[(user, dst)] += moves
        deviations[path[0][1]] -= moves
        deviations[path[-1][2]] += moves
    return {name: dev for name, dev in deviations.items() if dev != 0}


def _find_moves_path(
    cells: dict[Pair, int],
    values: dict[Pair, float],
    row_edges: dict[str, list[Pair]],
    deviations: dict[str, int],
) -> Optional[list[tuple[str, str, str]]]:
    """Find the shortest chain of unit moves from a surplus to a deficit account.

    Breadth-first search over accounts, where a unit can be moved from account a
    to account b through user u when u has a positive value on a and b is in the
    support of u.

    Returns:
        List of (user, source account, destination account) moves, or None
        when no surplus account can reach a deficit account.
    """
    sources = sorted(name for name, dev in deviations.items() if dev > 0)
    previous: dict[str, Optional[tuple[str, str]]] = {name: None for name in sources}
    queue = list(sources)
    for account in queue:
        for user, group in sorted(row_edges.items()):
            if cells.get((user, account), 0) <= 0:
                continue
            for _, other in group:
                if other in previous:
                    continue
                previous[other] = (user, account)
                if deviations.get(other, 0) < 0:
                    path: list[tuple[str, str, str]] = []
                    current = other
                    while previous[current] is not None:
                        via_user, from_account = previous[current]  # type: ignore[misc]
                        path.append((via_user, from_account, current))
                        current = from_account
                    return list(reversed(path))
                queue.append(other)
    return None


def build_weights(
    user_totals: dict[str, int],
    account_totals: dict[str, int],
    usage: dict[Pair, int],
    other_usage: dict[Pair, int],
    associations: set[Pair],
) -> tuple[dict[Pair, float], dict[str, list[str]]]:
    """Select user/account pairs and their weights to distribute consumption.

    Every user and account with a positive total gets pairs from the first
    available source, by order of preference:

    1. Slurm accounting usage of the resource, weighted by consumed seconds.
    2. Slurm accounting usage of the other resource, weighted by consumed
       seconds.
    3. Slurm associations, with equal weights.

    Pairs are limited to counterparts with a positive total, unless all totals
    of this side are zero.

    Returns:
        A tuple of weights per pair and of the mapping of sources to the list of
        users and accounts whose pairs come from this source. Users and accounts
        without pair from any source are listed under SOURCE_NONE.
    """
    users = {name for name, value in user_totals.items() if value > 0}
    accounts = {name for name, value in account_totals.items() if value > 0}

    def valid(pair: Pair) -> bool:
        user, account = pair
        if UNATTRIBUTED in pair:
            return False
        return (user in users or not users) and (account in accounts or not accounts)

    weights: dict[Pair, float] = {}
    sources: dict[str, list[str]] = {source: [] for source in SOURCES}

    def has_pairs(kind: str, name: str) -> bool:
        index = 0 if kind == "user" else 1
        return any(pair[index] == name for pair in weights)

    def candidates(kind: str, name: str, source: str) -> dict[Pair, float]:
        index = 0 if kind == "user" else 1
        if source == SOURCE_SACCT:
            pool = {pair: float(value) for pair, value in usage.items() if value > 0}
        elif source == SOURCE_SACCT_OTHER:
            pool = {
                pair: float(value) for pair, value in other_usage.items() if value > 0
            }
        else:
            pool = {pair: 1.0 for pair in associations}
        return {
            pair: value
            for pair, value in pool.items()
            if pair[index] == name and valid(pair)
        }

    entities = [("user", name) for name in sorted(users)] + [
        ("account", name) for name in sorted(accounts)
    ]
    for source in SOURCES:
        for kind, name in entities:
            if has_pairs(kind, name):
                continue
            found = candidates(kind, name, source)
            if found:
                weights.update(found)
                sources[source].append(f"{kind} {name}")
    sources[SOURCE_NONE] = [
        f"{kind} {name}" for kind, name in entities if not has_pairs(kind, name)
    ]
    return weights, sources


@dataclass
class Deviation:
    """User or account whose reallocated total differs from its unattributed
    consumption, or exceeds its quota after reallocation.

    Attributes:
        unattributed: Unattributed consumption in seconds.
        reallocated: Reallocated consumption in seconds.
        reasons: Reasons of the difference.
        negligible: Whether the difference is negligible, within
            NEGLIGIBLE_RATIO of the unattributed consumption.
        over_quota: Whether the reallocation makes the account exceed its quota.
    """

    unattributed: int
    reallocated: int
    reasons: list[str]
    negligible: bool = False
    over_quota: bool = False


@dataclass
class ReallocationReport:
    """Summary of a reallocation.

    Deviations are indexed by resource, then by "user <name>" or
    "account <name>".
    """

    pairs: int = 0
    reallocated_cpu_seconds: int = 0
    reallocated_gpu_seconds: int = 0
    sources: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    deviations: dict[str, dict[str, Deviation]] = field(default_factory=dict)

    def unpaired(self, resource: str) -> list[str]:
        """Users and accounts whose consumption is left unattributed."""
        return self.sources.get(resource, {}).get(SOURCE_NONE, [])

    def notable(self, resource: str) -> list[str]:
        """Paired users and accounts with a difference that is not negligible."""
        unpaired = self.unpaired(resource)
        return [
            name
            for name, deviation in self.deviations.get(resource, {}).items()
            if name not in unpaired and not deviation.negligible
        ]

    def over_quota(self, resource: str) -> list[str]:
        """Accounts exceeding their quota after reallocation."""
        return [
            name
            for name, deviation in self.deviations.get(resource, {}).items()
            if deviation.over_quota
        ]


def _minutes(seconds: int) -> int:
    """Round seconds to the nearest minute."""
    return (seconds + 30) // 60


def _is_negligible(difference: int, reference: int) -> bool:
    """Whether a difference is negligible compared to a reference value."""
    return abs(difference) <= reference * NEGLIGIBLE_RATIO


def _format_users(names: list[str], totals: dict[str, int]) -> str:
    """Format users with their consumption in minutes, the largest first."""
    ordered = sorted(names, key=lambda name: (-totals[name], name))
    result = ", ".join(
        f"{name} ({_minutes(totals[name])} minutes)"
        for name in ordered[:MAX_LISTED_USERS]
    )
    if len(ordered) > MAX_LISTED_USERS:
        result += f" and {len(ordered) - MAX_LISTED_USERS} more"
    return result


def _deviations(
    resource: str,
    user_totals: dict[str, int],
    account_totals: dict[str, int],
    fit: FitResult,
    unpaired: set[str],
    quotas: dict[str, int],
    default_quota: int,
    charged: dict[str, int],
) -> dict[str, Deviation]:
    """Return users and accounts whose reallocated total differs from their
    unattributed consumption, or exceeding their quota after reallocation, with
    the reasons.

    Unpaired users and accounts, identified as "user <name>" or
    "account <name>", are excluded from the fit and get nothing, their
    consumption is left unattributed. Users are listed first, then accounts by
    decreasing difference.

    Args:
        resource: "cpu" or "gpu".
        user_totals: Unattributed consumption per user, in seconds.
        account_totals: Unattributed consumption per account, in seconds.
        fit: Result of fit_consumption().
        unpaired: Unpaired users and accounts.
        quotas: Quota of the resource per account, in minutes, negative when
            unlimited.
        default_quota: Quota of accounts created by the reallocation.
        charged: Consumption of the resource per account on actual pairs,
            charged since the migration, in seconds.
    """
    reallocated_users: dict[str, int] = {}
    reallocated_accounts: dict[str, int] = {}
    accounts_of_users: dict[str, set[str]] = {}
    for (user, account), value in fit.pairs.items():
        reallocated_users[user] = reallocated_users.get(user, 0) + value
        reallocated_accounts[account] = reallocated_accounts.get(account, 0) + value
        accounts_of_users.setdefault(user, set()).add(account)

    deviations: dict[str, Deviation] = {}
    for name in sorted(set(user_totals) | set(reallocated_users)):
        unattributed = user_totals.get(name, 0)
        reallocated = reallocated_users.get(name, 0)
        if unattributed != reallocated:
            # User totals are always reached, unless the user has no pair or no
            # user has unattributed consumption.
            deviations[f"user {name}"] = Deviation(
                unattributed,
                reallocated,
                [
                    UNPAIRED_REASON
                    if f"user {name}" in unpaired
                    else "no user has unattributed consumption"
                ],
            )

    accounts: dict[str, Deviation] = {}
    for name in sorted(set(account_totals) | set(reallocated_accounts)):
        if f"account {name}" in unpaired:
            accounts[name] = Deviation(
                account_totals.get(name, 0), 0, [UNPAIRED_REASON]
            )
            continue
        unattributed = account_totals.get(name, 0)
        reallocated = reallocated_accounts.get(name, 0)
        deviation = Deviation(unattributed, reallocated, [])
        if unattributed != reallocated:
            target = fit.account_targets.get(name, 0)
            if not fit.account_targets:
                deviation.reasons.append("no account has unattributed consumption")
            elif _is_negligible(reallocated - unattributed, unattributed):
                deviation.negligible = True
            elif target != unattributed and _is_negligible(
                reallocated - target, target
            ):
                deviation.reasons.append(
                    "rescaled as overall unattributed consumption of users and "
                    "accounts differ"
                )
            else:
                # Users whose whole consumption is reallocated on this account.
                exclusive = [
                    user for user, names in accounts_of_users.items() if names == {name}
                ]
                if (
                    reallocated > unattributed
                    and exclusive
                    and sum(user_totals[user] for user in exclusive) > unattributed
                ):
                    deviation.reasons.append(
                        "users only paired with this account have more "
                        "unattributed consumption than this account: "
                        + _format_users(exclusive, user_totals)
                    )
                else:
                    deviation.reasons.append(
                        "users paired with this account do not allow to reach its total"
                    )
        quota = quotas.get(name, default_quota)
        before = charged.get(name, 0) + unattributed
        after = charged.get(name, 0) + reallocated
        if quota >= 0 and before <= quota * 60 < after:
            deviation.over_quota = True
            deviation.negligible = False
            deviation.reasons.append(
                f"exceeds its {resource.upper()} quota of {quota} minutes with "
                f"{_minutes(after)} minutes, jobs on this account will be refused"
            )
        if deviation.reasons or deviation.negligible:
            accounts[name] = deviation

    for name in sorted(
        accounts,
        key=lambda name: (
            -abs(
                _minutes(accounts[name].reallocated)
                - _minutes(accounts[name].unattributed)
            ),
            name,
        ),
    ):
        deviations[f"account {name}"] = accounts[name]
    return deviations


def _log_deviations(
    resource: str,
    user_totals: dict[str, int],
    account_totals: dict[str, int],
    fit: FitResult,
    unpaired: set[str],
    deviations: dict[str, Deviation],
) -> None:
    """Log warnings for the overall difference between users and accounts and
    for users and accounts in deviations."""
    total_users = sum(
        value for name, value in user_totals.items() if f"user {name}" not in unpaired
    )
    total_accounts = sum(
        value
        for name, value in account_totals.items()
        if f"account {name}" not in unpaired
    )
    if fit.account_targets and total_users and total_users != total_accounts:
        logger.warning(
            "%s overall unattributed consumption of users (%d minutes) differs "
            "from the one of accounts (%d minutes) by %+d minutes, account "
            "totals cannot all be preserved",
            resource.upper(),
            _minutes(total_users),
            _minutes(total_accounts),
            _minutes(total_users) - _minutes(total_accounts),
        )
    negligible: list[Deviation] = []
    for name, deviation in deviations.items():
        if deviation.negligible:
            negligible.append(deviation)
            continue
        logger.warning(
            "%s consumption of %s: %d minutes reallocated instead of %d minutes "
            "of unattributed consumption (%+d), %s",
            resource.upper(),
            name,
            _minutes(deviation.reallocated),
            _minutes(deviation.unattributed),
            _minutes(deviation.reallocated) - _minutes(deviation.unattributed),
            "; ".join(deviation.reasons),
        )
    if negligible:
        largest = max(
            abs(_minutes(deviation.reallocated) - _minutes(deviation.unattributed))
            for deviation in negligible
        )
        logger.warning(
            "%s consumption of %d account(s) differs from their unattributed "
            "consumption by less than %s%% (%s at most), due to the rounding of "
            "the distribution",
            resource.upper(),
            len(negligible),
            f"{NEGLIGIBLE_RATIO * 100:g}",
            f"{largest} minutes" if largest else "less than a minute",
        )


def _account_quotas(
    cursor: sqlite3.Cursor, resource: str
) -> tuple[dict[str, int], dict[str, int]]:
    """Return the quota and the consumption charged on actual pairs per account.

    Args:
        cursor: Database cursor.
        resource: "cpu" or "gpu".

    Returns:
        A tuple of the quota in minutes per account, negative when unlimited,
        and of the consumption in seconds charged on actual pairs per account.
    """
    cursor.execute(f"SELECT account, quota_{resource}_minutes FROM accounts")
    quotas: dict[str, int] = {
        str(name): int(quota) for name, quota in cursor.fetchall()
    }
    cursor.execute(
        f"SELECT account, SUM(consumed_{resource}_seconds) "
        "FROM user_account_consumption "
        "WHERE username != ? AND account != ? GROUP BY account",
        (UNATTRIBUTED, UNATTRIBUTED),
    )
    charged = {str(name): int(value) for name, value in cursor.fetchall()}

    return quotas, charged


def _unattributed_totals(
    cursor: sqlite3.Cursor, column: str
) -> tuple[dict[str, int], dict[str, int]]:
    cursor.execute(
        f"SELECT username, {column} FROM user_account_consumption WHERE account = ?",
        (UNATTRIBUTED,),
    )
    user_totals = {
        str(name): int(value)
        for name, value in cursor.fetchall()
        if name != UNATTRIBUTED
    }
    cursor.execute(
        f"SELECT account, {column} FROM user_account_consumption WHERE username = ?",
        (UNATTRIBUTED,),
    )
    account_totals = {
        str(name): int(value)
        for name, value in cursor.fetchall()
        if name != UNATTRIBUTED
    }
    return user_totals, account_totals


def has_unattributed_consumption() -> bool:
    """Return whether the database contains unattributed consumption pairs."""
    if not os.path.exists(slurm_quota.DB_PATH):
        return False
    with connect_database() as conn:
        row = conn.execute(
            "SELECT 1 FROM user_account_consumption "
            "WHERE username = ? OR account = ? LIMIT 1",
            (UNATTRIBUTED, UNATTRIBUTED),
        ).fetchone()
    return row is not None


def reallocate_unattributed(
    usage: dict[Pair, tuple[int, int]],
    associations: set[Pair],
    dry_run: bool = False,
) -> ReallocationReport:
    """Reallocate unattributed consumption to user/account pairs.

    For CPU and GPU separately, unattributed consumption of users and accounts
    is distributed among pairs selected and weighted by build_weights(), with
    fit_consumption(). The resulting consumption is added to the pairs and
    removed from unattributed pairs. Consumption of users and accounts without
    any pair is left unattributed, for a later reallocation. Unattributed pairs
    without consumption left are removed, then the unattributed user and
    account when no pair refers to them anymore. Everything runs in a single
    transaction, rolled back in dry-run mode.

    Args:
        usage: Consumed (CPU, GPU) seconds per (user, account) pair from Slurm
            accounting.
        associations: (user, account) associations defined in Slurm.
        dry_run: Compute and report without modifying the database.

    Returns:
        Reallocation report.
    """
    report = ReallocationReport()
    conn = connect_database()
    conn.isolation_level = None  # explicit transaction control
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        dq = get_default_quota_settings(conn)
        pairs: dict[Pair, list[int]] = {}
        reallocated_users: dict[str, set[str]] = {}
        reallocated_accounts: dict[str, set[str]] = {}
        for index, resource in enumerate(("cpu", "gpu")):
            column = f"consumed_{resource}_seconds"
            user_totals, account_totals = _unattributed_totals(cursor, column)
            weights, sources = build_weights(
                user_totals,
                account_totals,
                {pair: values[index] for pair, values in usage.items()},
                {pair: values[1 - index] for pair, values in usage.items()},
                associations,
            )
            unpaired = set(sources[SOURCE_NONE])
            try:
                fit = fit_consumption(
                    {
                        name: value
                        for name, value in user_totals.items()
                        if f"user {name}" not in unpaired
                    },
                    {
                        name: value
                        for name, value in account_totals.items()
                        if f"account {name}" not in unpaired
                    },
                    weights,
                )
            except ReallocationError as err:
                raise ReallocationError(
                    f"{resource.upper()} consumption: {err}"
                ) from err
            reallocated = sum(fit.pairs.values())
            if resource == "cpu":
                report.reallocated_cpu_seconds = reallocated
            else:
                report.reallocated_gpu_seconds = reallocated
            reallocated_users[column] = {
                name for name in user_totals if f"user {name}" not in unpaired
            }
            reallocated_accounts[column] = {
                name for name in account_totals if f"account {name}" not in unpaired
            }
            quotas, charged = _account_quotas(cursor, resource)
            deviations = _deviations(
                resource,
                user_totals,
                account_totals,
                fit,
                unpaired,
                quotas,
                dq[f"default_account_quota_{resource}_minutes"],
                charged,
            )
            report.sources[resource] = sources
            report.deviations[resource] = deviations
            _log_deviations(
                resource, user_totals, account_totals, fit, unpaired, deviations
            )
            for pair, value in fit.pairs.items():
                pairs.setdefault(pair, [0, 0])[index] = value

        for (user, account), (cpu, gpu) in sorted(pairs.items()):
            logger.debug(
                "Reallocate to user %s on account %s: %ds CPU, %ds GPU",
                user,
                account,
                cpu,
                gpu,
            )
            cursor.execute(
                """
                INSERT INTO users (username, quota_cpu_minutes, quota_gpu_minutes)
                VALUES (?, ?, ?)
                ON CONFLICT (username) DO NOTHING
                """,
                (
                    user,
                    dq["default_user_quota_cpu_minutes"],
                    dq["default_user_quota_gpu_minutes"],
                ),
            )
            cursor.execute(
                """
                INSERT INTO accounts (account, quota_cpu_minutes, quota_gpu_minutes)
                VALUES (?, ?, ?)
                ON CONFLICT (account) DO NOTHING
                """,
                (
                    account,
                    dq["default_account_quota_cpu_minutes"],
                    dq["default_account_quota_gpu_minutes"],
                ),
            )
            cursor.execute(
                """
                INSERT INTO user_account_consumption (
                    username, account, consumed_cpu_seconds, consumed_gpu_seconds,
                    last_updated
                )
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT (username, account) DO
                UPDATE SET consumed_cpu_seconds = consumed_cpu_seconds + ?,
                           consumed_gpu_seconds = consumed_gpu_seconds + ?,
                           last_updated = CURRENT_TIMESTAMP
                """,
                (user, account, cpu, gpu, cpu, gpu),
            )
        report.pairs = len(pairs)

        # Remove reallocated consumption from unattributed pairs, consumption of
        # unpaired users and accounts is left unattributed.
        for column, names in reallocated_users.items():
            cursor.executemany(
                f"UPDATE user_account_consumption SET {column} = 0 "
                "WHERE username = ? AND account = ?",
                [(name, UNATTRIBUTED) for name in sorted(names)],
            )
        for column, names in reallocated_accounts.items():
            cursor.executemany(
                f"UPDATE user_account_consumption SET {column} = 0 "
                "WHERE username = ? AND account = ?",
                [(UNATTRIBUTED, name) for name in sorted(names)],
            )
        cursor.execute(
            """
            DELETE FROM user_account_consumption
            WHERE (username = ? OR account = ?)
              AND consumed_cpu_seconds = 0 AND consumed_gpu_seconds = 0
            """,
            (UNATTRIBUTED, UNATTRIBUTED),
        )
        cursor.execute(
            """
            DELETE FROM users WHERE username = ? AND NOT EXISTS (
                SELECT 1 FROM user_account_consumption WHERE username = ?
            )
            """,
            (UNATTRIBUTED, UNATTRIBUTED),
        )
        cursor.execute(
            """
            DELETE FROM accounts WHERE account = ? AND NOT EXISTS (
                SELECT 1 FROM user_account_consumption WHERE account = ?
            )
            """,
            (UNATTRIBUTED, UNATTRIBUTED),
        )

        if dry_run:
            cursor.execute("ROLLBACK")
        else:
            cursor.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            cursor.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return report


def _parse_time(value: str) -> int:
    """Parse a local ISO 8601 date or date and time into epoch seconds."""
    try:
        return int(datetime.datetime.fromisoformat(value).timestamp())
    except ValueError as err:
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r}, expected YYYY-MM-DD[THH:MM[:SS]]"
        ) from err


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as err:
        raise argparse.ArgumentTypeError(f"invalid integer {value!r}") from err
    if number <= 0:
        raise argparse.ArgumentTypeError(f"must be positive, not {number}")
    return number


def _print_report(report: ReallocationReport, dry_run: bool) -> None:
    prefix = "Dry-run: would reallocate" if dry_run else "Reallocated"
    print(
        f"{prefix} {report.reallocated_cpu_seconds // 60} CPU minutes and "
        f"{report.reallocated_gpu_seconds // 60} GPU minutes of unattributed "
        f"consumption to {report.pairs} user/account pair(s)"
    )
    for resource in ("cpu", "gpu"):
        sources = report.sources.get(resource, {})
        counts = ", ".join(
            f"{source} {len(sources.get(source, []))}" for source in SOURCES
        )
        print(f"{resource.upper()} users and accounts paired from: {counts}")
        unpaired = report.unpaired(resource)
        notable = report.notable(resource)
        if notable:
            print(
                f"{resource.upper()} users and accounts not matching their "
                f"unattributed consumption: {len(notable)} (see warnings)"
            )
        over_quota = report.over_quota(resource)
        if over_quota:
            print(
                f"{resource.upper()} accounts exceeding their quota after "
                f"reallocation: {len(over_quota)} (see warnings)"
            )
        if unpaired:
            print(
                f"{resource.upper()} users and accounts left unattributed: "
                f"{len(unpaired)} (see warnings, run again with another period "
                "to reallocate them)"
            )


def reallocate_command(
    starttime: int,
    endtime: Optional[int],
    chunk_days: int,
    dry_run: bool,
) -> None:
    """Execute the reallocation.

    This command can only be executed by root, unless another database is
    selected with the DB_PATH_ENV environment variable, for example to test
    the reallocation on a copy of the production database.
    """
    db_path = os.environ.get(DB_PATH_ENV)
    if db_path:
        if not os.path.exists(db_path):
            logger.error(f"Database file not found: {db_path}")
            sys.exit(1)
        slurm_quota.DB_PATH = db_path
        logger.warning("Using database %s set in %s", db_path, DB_PATH_ENV)
    current_user = auth.get_current_user()
    if not db_path and current_user != "root":
        logger.error(
            "Reallocate command can only be executed by root user, "
            f"not by {current_user}"
        )
        sys.exit(1)

    try:
        if not has_unattributed_consumption():
            print("Nothing to reallocate")
            return

        if endtime is None:
            endtime = int(time.time())
        if starttime >= endtime:
            logger.error("Start time must be before end time")
            sys.exit(1)

        # Query Slurm before opening the database transaction.
        usage = slurm_integration.collect_pair_usage_from_sacct(
            starttime, endtime, chunk_days, load_gpu_factors()
        )
        logger.info(
            "Found %d user/account pairs with consumption in Slurm accounting",
            len(usage),
        )
        associations = slurm_integration.get_associations()
        report = reallocate_unattributed(usage, associations, dry_run=dry_run)
    except (
        ReallocationError,
        slurm_integration.SlurmQueryError,
        sqlite3.Error,
    ) as err:
        logger.error(f"Reallocation failed: {err}")
        sys.exit(1)

    _print_report(report, dry_run)


def main() -> None:
    """Main entry point for the slurm-quota-reallocate script."""
    parser = argparse.ArgumentParser(
        prog="slurm-quota-reallocate",
        description=(
            "Reallocate unattributed legacy consumption to user/account pairs "
            "based on Slurm accounting"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_common_arguments(parser)
    parser.add_argument(
        "--starttime",
        type=_parse_time,
        required=True,
        help="Consider jobs ending after this date (YYYY-MM-DD[THH:MM[:SS]])",
    )
    parser.add_argument(
        "--endtime",
        type=_parse_time,
        help=(
            "Consider jobs ending before this date (YYYY-MM-DD[THH:MM[:SS]], "
            "default: now)"
        ),
    )
    parser.add_argument(
        "--chunk-days",
        type=_positive_int,
        default=30,
        help="Length in days of Slurm accounting queries (default: 30)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the result of reallocation without modifying the database",
    )
    args = parser.parse_args()
    setup_logging(args)
    reallocate_command(args.starttime, args.endtime, args.chunk_days, args.dry_run)
