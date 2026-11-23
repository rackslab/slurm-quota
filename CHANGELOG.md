# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Consumption is recorded for each user on each account, so administrators know which user consumed on which account
  (#110).
- `slurm-quota-reallocate` command to attribute the legacy consumption migrated from previous versions to users on
  accounts, based on Slurm accounting (#110).
- Consumption breakdown by user and account:
  - `breakdown` query parameter of `GET /stats` REST API route.
  - `slurm-quota stats` breaks down the consumption of a single user by account and of a single account by user, with
    new `--breakdown` and `--no-breakdown` options.
  - Expandable user and account rows in web dashboard, with buttons to adjust the consumption of a user on an account
    for admins and operators.
- Seconds unit:
  - `slurm-quota stats --seconds` and `slurm-quota adjust --seconds` options.
  - Seconds unit in web dashboard and delta unit selector in consumption adjustment dialog.
- `slurm-quota-sync` command, run by `slurm-quota-sync.timer` systemd timer every 5 minutes, to cache Slurm user/account
  associations in the database. Added and removed associations are logged.
- `job_submit.lua` refuses jobs and account modifications when the user is not associated with the account in Slurm,
  based on the associations cached by `slurm-quota-sync` (#107). The check can be disabled with `CHECK_ASSOCIATIONS`.
- Application tokens with scopes, for scripts and integrations (#111):
  - Token scopes checked on all REST API routes.
  - `/tokens` REST API routes to create, list and revoke application tokens.
  - `slurm-quota token create|list|revoke` subcommands.
  - API tokens page in web dashboard.
  - `jwt.app_duration` (default 90 days) and `jwt.app_max_duration` (default 365 days) settings.
- Comments on users and accounts (#112):
  - `PUT /comments/users/<username>` and `PUT /comments/accounts/<account>` REST API routes, with new `comments:write`
    scope for operators and admins. Comments are returned in `GET /stats`.
  - `slurm-quota comments` command and `slurm-quota stats --comments` option.
  - Comments display and edition dialog in web dashboard.
  - Reasons of quota changes and consumption adjustments are appended to comments, unless `append_reason` is false in
    REST API, `--no-save-comment` option is given to CLI or the _Append reason to comment_ checkbox is unchecked in web
    dashboard.
- `debug` parameter in `[log]` section of `slurm-quota-serve` configuration (#94).
- Logs of logins, failed logins, role and manager accounts changes and denied accesses in `slurm-quota-serve` and
  `slurm-quota-web` (#104).
- Role of the signed-in user displayed in web dashboard header (#105).
- `slurm-quota-prune --user` and `--account` filters also apply to orphan preallocations with `--preallocs` (#109).
- Bash completion of new commands, subcommands and options.

### Changed

- Jobs are charged and preallocated with second precision instead of minute precision. Jobs shorter than one minute are
  not free anymore. Quotas are still defined in minutes (#110).
- `GET /stats` REST API route reports consumed and preallocated times in seconds, in `total_consumed_{cpu,gpu}_seconds`
  and `total_preallocated_{cpu,gpu}_seconds` fields.
- Consumption is adjusted for a user on an account with `PATCH /consumption/<username>/<account>/<resource>` REST API
  route, with `delta_seconds` instead of `delta_minutes`, and responds with `total_consumed_seconds`.
  `slurm-quota adjust` requires both `--user` and `--account` (#110).
- `GET /stats` reads Slurm associations from the database cache instead of querying `sacctmgr` on each request.

### Documentation

- Upgrade guide from version 3 to version 4.
- `--reason` option of quota and adjust commands in `slurm-quota` man page (#108).

## [3.0.1] - 2026-08-25

### Fixed

- Warning or error logged by `slurm-quota-serve` when a REST API request is aborted (#96).
- Error messages returned by the REST API are displayed in CLI and web dashboard (#97).
- Less restrictive validation of user and account names, special characters such as `@` are now accepted (#98).
- User and account names are quoted in REST API URL paths.

## [3.0.0] - 2026-07-09

### Added

- `slurm-quota-charge` and `slurm-quota-serve` commands, split from `slurm-quota`.
- `slurm-quota-prune` command, split from `slurm-quota prune`.
- Mandatory authentication on REST API with JWT tokens:
  - LDAP authentication with `slurm-quota login` command.
  - `slurm-quota-token` command for root to issue tokens without LDAP directory.
  - Login page in web dashboard.
  - `slurm-quota token` command to display token information, `--save` to save a token from environment.
- Role-based access control (#40, #63):
  - _admin_ role, managing roles.
  - _operator_ role, viewing all statistics and changing quotas.
  - _manager_ role, viewing statistics of assigned accounts and their users.
  - _user_ role, viewing own statistics.
  - `slurm-quota role` command and roles page in web dashboard.
- REST API routes to change quotas, adjust consumption, manage default quotas and GPU factors, used by `slurm-quota` CLI
  and web dashboard. Quotas and consumption can be changed from web dashboard.
- Optional reason of quota changes and consumption adjustments, logged by `slurm-quota-serve` (`--reason` option in
  CLI).
- Native TLS support in REST API server, with `SLURM_QUOTA_CA_CERT` environment variable for custom CA in CLI and web
  dashboard.
- Arguments and settings to control log flags.
- Bash completion for `slurm-quota-prune` and `slurm-quota-token`.

### Changed

- Re-licensed under GPLv2+.
- `slurm-quota-web` rewritten as a Python application, loading an environment file in WSGI app.
- REST API server based on Flask, run as `slurm` user and initializing the database.
- Migration command renamed `slurm-quota-migrate` for consistency.
- SQLite database in WAL mode.
- Default token and web session duration increased to 7 days.
- Many web dashboard UI/UX improvements, with flash messages to report successes and errors.

### Fixed

- Database closed before calling `squeue` during pruning.
- Expired or missing token detected in CLI.
- Redirection with URL prefix after web login.
- `slurm-quota stats` requires a token.
- Managers inherit statistics permissions of users.

### Documentation

- Upgrade guide from version 2 to version 3.
- README.md split into multiple documents, with installation guide.

## [2.0.0] - 2026-04-20

### Added

- Users and accounts eligible for pruning are logged.
- `-q, --quiet` option.
- `prune --user` and `--account` filters.

## [2.0.0rc1] - 2026-04-14

### Added

- Infinite service idle timeout support.
- `slurm-quota stats --hours` option.
- Statistics of a specific account.
- Default quotas stored in settings table and loaded by `job_submit.lua`.
- `adjust` command to add or subtract CPU/GPU time.
- Pruning of users and accounts without consumption.
- `slurm-quota --version` option.
- `slurm-quota-web` web dashboard.
- Bash completion for `slurm-quota`.
- Man pages for `slurm-quota` and `slurm-quota-web`.

### Fixed

- Width of first column of statistics.
- Completion of partially typed options.

[2.0.0]: https://github.com/rackslab/slurm-quota/compare/v2.0.0rc1...v2.0.0
[2.0.0rc1]: https://github.com/rackslab/slurm-quota/compare/v1.0.0...v2.0.0rc1
[3.0.0]: https://github.com/rackslab/slurm-quota/compare/v2.0.0...v3.0.0
[3.0.1]: https://github.com/rackslab/slurm-quota/compare/v3.0.0...v3.0.1
[unreleased]: https://github.com/rackslab/slurm-quota/compare/v3.0.1...HEAD
