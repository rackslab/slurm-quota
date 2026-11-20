# Upgrade from version 3 to 4

This procedure applies to existing RPM-based version 3 installations. Version 4 keeps the same quota database path but
changes how consumption is recorded in the database, which requires a short maintenance of the Slurm controller.

For new installations, follow the main [Installation](../README.md#installation) guide instead.

## What changed

- Consumption is recorded per user/account pair, in seconds, instead of separately for users and accounts in minutes.
  User and account consumptions are the sums over their pairs. Quotas remain defined in minutes at user and account
  levels.

- Jobs are charged with second precision: jobs shorter than one minute are now charged.

- Preallocations of pending and running jobs are recorded in seconds. GPU preallocations are rounded up to the second,
  instead of down to the minute, so they are never lower than the charged consumption.

- The REST API reports consumed and preallocated times in seconds, and accepts consumption adjustments in seconds.
  Quotas remain in minutes:

  - `GET /stats` fields `total_consumed_{cpu,gpu}_minutes` and `total_preallocated_{cpu,gpu}_minutes` are replaced by
    `total_consumed_{cpu,gpu}_seconds` and `total_preallocated_{cpu,gpu}_seconds`.
  - Consumption adjustments accept `delta_seconds` instead of `delta_minutes`, and respond with `total_consumed_seconds`
    instead of `total_consumed_minutes`.

  Scripts using these fields must be updated, for example divided by 60 to get minutes, or multiplied by 60 to send
  deltas in seconds. `slurm-quota stats` and the web dashboard still display minutes or hours, but compute status bars
  and usage percentages in seconds, so they match the quota check at job submission. `slurm-quota adjust` and the web
  dashboard still take adjustments in minutes or hours.

- Consumption adjustments target a user/account pair:

  - `slurm-quota adjust` requires both `--user` and `--account`.
  - The REST API endpoint is `PATCH /consumption/<username>/<account>/<resource>`, replacing
    `PATCH /consumption/<user|account>/<name>/<resource>`.
  - The web dashboard consumption dialog asks for the other side of the pair.

- The database migration, run automatically by the `slurm-quota-controller` RPM upgrade when
  `/var/lib/state/slurm-quota/slurm-quota.db` exists, converts existing jobs preallocations to seconds, moves
  consumption to the new `user_account_consumption` table and removes consumption columns from `users` and `accounts`
  tables, all in a single transaction. Version 3 databases only store consumption separately for each user and each
  account. Which user consumed on which account is not recorded, and the migration does not query Slurm. Legacy
  consumption is therefore stored on temporary pairs with a special `(unattributed)` user and account:

  - the legacy consumption of each user is stored on pair (user, `(unattributed)`),
  - the legacy consumption of each account is stored on pair (`(unattributed)`, account).

  User and account consumptions are preserved exactly, and quotas are enforced as before the migration. The
  `(unattributed)` user and account are visible as regular entries in statistics until they are reallocated. The time of
  the migration is recorded in the database. The migration runs in a single transaction: on any error, the database is
  left unchanged. It fails if a user or an account named `(unattributed)` already exists.

- Version 3 `job_submit.lua` cannot read the version 4 database schema, and version 3 `slurm-quota-charge` cannot write
  it. The database migration and the new versions of these files are all installed by the `slurm-quota-controller`
  package, they must not run concurrently with job submissions and completions. This is why the controller upgrade
  requires a maintenance window, described in the following steps.

- Slurm associations are cached in the database by the new `slurm-quota-sync` command, run every 5 minutes by the
  `slurm-quota-sync.timer` systemd timer, both installed by the `slurm-quota-controller` package:

  - `job_submit.lua` refuses jobs submitted or modified with an account the user is not associated with, for example a
    mistyped account, instead of recording this account in the database. Associations must be synchronized before
    `slurmctld` loads the version 4 `job_submit.lua`, otherwise all jobs are refused. The check can be disabled by
    setting `CHECK_ASSOCIATIONS = false` at the top of the script.
  - `slurm-quota-serve` reads the accounts of users and the users of accounts in statistics from the cache, instead of
    querying `sacctmgr` on each request. Until associations are synchronized, regular users and managers see no Slurm
    account in statistics.

  After adding associations with `sacctmgr`, run `sudo systemctl start slurm-quota-sync.service` so that jobs of new
  users are not refused until the next periodic synchronization.

- The new `slurm-quota-reallocate` command, installed by the `slurm-quota-controller` package, attributes legacy
  consumption to actual user/account pairs based on Slurm accounting, then removes the `(unattributed)` pairs, user and
  account once all their consumption is reallocated. It can be run any time after the upgrade, see
  [step 5](#5-reallocate-unattributed-consumption).

- Users can create longer-lived [application tokens](application-tokens.md) with restricted
  [scopes](../README.md#token-scopes) for scripts and integrations. Tokens issued by version 3 remain valid until they
  expire.

## 1. Plan a maintenance reservation

A maintenance reservation on all nodes is recommended for the controller upgrade. `slurm-quota-charge` runs on every job
completion and writes consumption in the database, it must not run while the database is migrated. The reservation
ensures no job is running during the upgrade, so no job completes in the meantime. It also ensures the database backup
includes all charged consumption, so restoring it in case of problem loses nothing.

Create the reservation ahead of the maintenance window, for example:

```bash
sudo scontrol create reservation ReservationName=slurm-quota-v4 \
    StartTime=2026-12-01T08:00:00 Duration=02:00:00 \
    Nodes=ALL Users=root Flags=MAINT
```

Slurm does not start jobs that would still be running at the start of the reservation. When the maintenance window
starts, check that no job is running anymore with `squeue --states=RUNNING,COMPLETING`.

The reservation does not prevent job submissions, which are processed by `job_submit.lua`. They are blocked by stopping
`slurmctld` during the controller upgrade, in the next step.

## 2. Upgrade the controller node

Stop `slurmctld` to block job submissions, and stop the REST API to block quota and consumption changes:

```bash
sudo systemctl stop slurmctld
sudo systemctl stop slurm-quota.socket slurm-quota.service
```

The database migration cannot be reverted. Backup the SQLite database:

```bash
sudo sqlite3 /var/lib/state/slurm-quota/slurm-quota.db ".backup /var/lib/state/slurm-quota/slurm-quota-v3-backup-$(date +%Y-%m-%d).db"
```

Upgrade the common and controller packages:

```bash
sudo dnf upgrade slurm-quota slurm-quota-controller
```

The `slurm-quota-controller` package upgrade automatically runs the database migration. Check its output in the `dnf`
transaction logs, it reports legacy consumption stored on `(unattributed)` pairs. If the migration fails, the database
is left unchanged and the scriptlet error is reported by `dnf`. Fix the reported issue, then run the migration manually,
before starting `slurmctld`:

```bash
sudo /usr/libexec/slurm-quota/slurm-quota-migrate
```

> [!IMPORTANT]
> `/etc/slurm/job_submit.lua` is a configuration file of the `slurm-quota-controller` package. If it was modified
> locally, the upgrade keeps it and installs version 4 as `/etc/slurm/job_submit.lua.rpmnew`. Version 3 `job_submit.lua`
> cannot read the version 4 database: on database errors, it allows job submissions without checking quotas. In this
> case, merge your local changes into the version 4 file and replace `/etc/slurm/job_submit.lua` with it before starting
> `slurmctld`.

```bash
ls /etc/slurm/job_submit.lua.rpmnew
```

Synchronize Slurm associations in the database and enable the periodic synchronization. `slurmdbd` must be running for
`sacctmgr` queries:

```bash
sudo systemctl start slurm-quota-sync.service
sudo systemctl enable --now slurm-quota-sync.timer
```

> [!IMPORTANT]
> Do not start `slurmctld` until the synchronization succeeds: with an empty associations cache, version 4
> `job_submit.lua` refuses all jobs. `systemctl start` fails when the synchronization fails. In this case, check the
> logs with `journalctl -u slurm-quota-sync.service`, fix the reported issue and run the synchronization again.

Check that the number of synchronized associations reported in the logs matches the number of user associations in
Slurm:

```bash
sudo journalctl -u slurm-quota-sync.service --grep Synchronized --lines 1
sacctmgr --noheader --parsable2 list associations format=user,account | grep --count --invert-match '^|'
```

Start the REST API and `slurmctld`, which loads the version 4 `job_submit.lua`:

```bash
sudo systemctl start slurm-quota.socket
sudo systemctl start slurmctld
```

Check the API health endpoint and statistics on the controller node. Legacy consumption appears with the
`(unattributed)` user and account:

```bash
curl https://127.0.0.1:9911/health
slurm-quota stats
```

## 3. Release the maintenance reservation

Delete the reservation, pending jobs are scheduled again:

```bash
sudo scontrol delete ReservationName=slurm-quota-v4
```

## 4. Upgrade compute, login and web dashboard nodes

On compute and login nodes, upgrade the common package:

```bash
sudo dnf upgrade slurm-quota
```

If the optional web dashboard is installed on a node, upgrade it too and reload Apache so that mod_wsgi loads the new
version:

```bash
sudo dnf upgrade slurm-quota-web
sudo systemctl reload httpd
```

These nodes do not access the database, they can be upgraded after the maintenance window. Until then, version 3
consumption adjustments fail with an API error.

## 5. Reallocate unattributed consumption

After the upgrade, the legacy consumption of users and accounts is held by an `(unattributed)` user and account, visible
in statistics. Run `slurm-quota-reallocate` on the controller node to attribute it to actual user/account pairs. It does
not require a maintenance window and can be run any time after the upgrade, quotas are enforced correctly meanwhile.

### Reallocation logic

The command queries Slurm accounting (`sacct`) for jobs ending in the selected period, until the migration by default.
For CPU and GPU separately, it determines the actual user/account pairs and their shares from these jobs, distributes
unattributed consumption accordingly while preserving user totals and, as far as possible, account totals (see
[limits](#limits)), and removes the reallocated `(unattributed)` pairs, then the `(unattributed)` user and account once
all their consumption is reallocated. The period does not need to cover all legacy consumption, it must be
representative of how users consumed on their accounts, for example the period since the last reset of consumptions.

For example, consider this legacy CPU consumption after the migration, in minutes:

| User/account pair      | CPU minutes |
| ---------------------- | ----------: |
| alice/`(unattributed)` |         600 |
| bob/`(unattributed)`   |         300 |
| carol/`(unattributed)` |         300 |
| `(unattributed)`/hpc   |         600 |
| `(unattributed)`/dev   |         600 |

In the selected period, Slurm accounting reports these jobs CPU consumption, in minutes:

| User/account pair | CPU minutes in `sacct` |
| ----------------- | ---------------------: |
| alice/hpc         |                    150 |
| alice/dev         |                     50 |
| bob/dev           |                    100 |
| carol/hpc         |                     50 |
| carol/dev         |                     50 |

Alice consumed three quarters on hpc and one quarter on dev, Bob only on dev, Carol half on each account. Unattributed
consumption is distributed among these pairs with these shares, while preserving user and account totals:

| User/account pair | CPU minutes |
| ----------------- | ----------: |
| alice/hpc         |         450 |
| alice/dev         |         150 |
| bob/dev           |         300 |
| carol/hpc         |         150 |
| carol/dev         |         150 |

Alice total is still 600 minutes, Bob 300 and Carol 300, hpc total is still 600 minutes (450 + 150) and dev 600 (150 +
300 + 150). When Slurm accounting shares are not consistent with legacy totals, shares are adjusted as little as
possible to preserve these totals. Reallocated consumption is added to consumption charged on these pairs since the
upgrade.

### Usage

Review the result first with `--dry-run`:

```bash
sudo slurm-quota-reallocate --starttime 2025-01-01 --dry-run
```

Then apply it:

```bash
sudo slurm-quota-reallocate --starttime 2025-01-01
```

The reallocation runs in a single transaction: either it is applied entirely, or the database is left unchanged on error
and the command can simply be run again. Once it has succeeded, further runs only consider consumption left unattributed
(see [limits](#limits)). When all unattributed consumption is reallocated, further runs report there is nothing to
reallocate and do not modify the database.

> [!WARNING]
> Reallocated consumption cannot be reallocated again with another period, as it is merged with consumption charged on
> actual pairs since the upgrade. Review the result with `--dry-run` before applying it.

To test the reallocation without risk, run it on a copy of the database by setting `SLURM_QUOTA_DB` to its path. The
command does not need to run as `root` in this case:

```bash
sudo sqlite3 /var/lib/state/slurm-quota/slurm-quota.db ".backup /tmp/slurm-quota-copy.db"
sudo chown $USER /tmp/slurm-quota-copy.db
SLURM_QUOTA_DB=/tmp/slurm-quota-copy.db slurm-quota-reallocate --starttime 2025-01-01
```

### Limits

- User totals are preserved exactly: the reallocated consumption of each user equals its legacy consumption, to the
  second, unless it is left unattributed as explained below. Account totals are preserved exactly only when both of
  these conditions are met:

  - Overall user and account consumptions are equal. Version 3 adjustments that targeted only users or only accounts
    break this equality, then both cannot be preserved. In this case, account totals are first rescaled proportionally
    so that they sum to the overall user consumption. For example, if users sum to 1000 minutes and accounts to 1100
    minutes, an account with 550 minutes gets 500 minutes.
  - The user/account pairs make account totals reachable. For example, if Alice has 100 minutes of unattributed
    consumption, only consumed on account hpc in the selected period, and hpc has only 60 minutes of unattributed
    consumption, Alice 100 minutes are still all reallocated on hpc, which exceeds its legacy consumption by 40 minutes,
    and the other accounts get 40 minutes less in total.

  Otherwise, some accounts get more or less than their legacy consumption. For each of them, a warning gives the
  reallocated and legacy consumptions and the reason, for example:

  ```console
  CPU consumption of account hpc: 7200 seconds reallocated instead of 5400 seconds of unattributed consumption (+1800), users paired with this account do not allow to reach its total
  ```

  The number of users and accounts whose reallocated consumption differs from their legacy consumption is reported at
  the end of the reallocation.

- Users and accounts without jobs in the selected period get their pairs from Slurm associations (`sacctmgr`). Users and
  accounts without jobs nor association, for example users removed from Slurm, cannot be reallocated: their consumption
  is left unattributed, with a warning for each of them:

  ```console
  CPU consumption of user dave: 0 seconds reallocated instead of 7200 seconds of unattributed consumption (-7200), no job in Slurm accounting in the selected period nor Slurm association found, its consumption is left unattributed
  ```

  Their number is reported at the end of the reallocation. Their consumption stays visible on the `(unattributed)` user
  and account, and quotas are still enforced with it. Run the command again with another period, or after adding the
  missing Slurm associations, to reallocate it.

- GPU shares are computed with the current GPU factors, as their history is not kept. If factors changed during the
  selected period, shares can be biased for users who used different GPU types on different accounts. Select a period
  that starts after the last change of GPU factors to avoid this.

## 6. Update scripts using consumption adjustments

Scripts calling `slurm-quota adjust` or the `/consumption` REST API endpoint must target a user/account pair, for
example:

```diff
- slurm-quota adjust --user alice --cpu --minutes=+30
+ slurm-quota adjust --user alice --account hpc --cpu --minutes=+30
```
