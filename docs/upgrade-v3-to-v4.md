# Upgrade from version 3 to 4

This procedure applies to existing version 3 installations.

## What changed

- Consumption is recorded per user/account pair, in seconds, instead of separately for users and accounts in minutes.
  User and account consumptions are the sums over their pairs. Quotas remain defined in minutes at user and account
  levels.
- Jobs are charged with second precision: jobs shorter than one minute are now charged.
- Consumption adjustments target a user/account pair:
  - `slurm-quota adjust` requires both `--user` and `--account`.
  - The REST API endpoint is `PATCH /consumption/<username>/<account>/<resource>`, replacing
    `PATCH /consumption/<user|account>/<name>/<resource>`.
    - The web dashboard consumption dialog asks for the other side of the pair.
- Legacy consumption is stored on temporary pairs with an `(unattributed)` user and account by the database migration.
  The new `slurm-quota-reallocate` command attributes it to actual user/account pairs based on Slurm accounting.

## 1. Backup the database

On the controller node, backup the SQLite database before upgrading packages:

```bash
sudo cp -a /var/lib/state/slurm-quota/slurm-quota.db /var/lib/state/slurm-quota/slurm-quota.db.v3
```

## 2. Migrate the database

The database migration stores legacy consumption on temporary `(unattributed)` pairs, as described in
[database migrations](database-migrations.md#consumption-per-useraccount-pair-v4). With RPM packages, the migration runs
automatically on `slurm-quota-controller` upgrade. Otherwise, run it manually:

```bash
sudo slurm-quota-migrate
```

## 3. Update `job_submit.lua` and slurm-quota

Version 3 `job_submit.lua` cannot read the version 4 database schema, and the opposite. Update `job_submit.lua` and
slurm-quota components together, right after the database migration, then reconfigure Slurm:

```bash
sudo scontrol reconfigure
```

## 4. Update scripts using consumption adjustments

Scripts calling `slurm-quota adjust` or the `/consumption` REST API endpoint must target a user/account pair, for
example:

```diff
- slurm-quota adjust --user alice --cpu --minutes=+30
+ slurm-quota adjust --user alice --account hpc --cpu --minutes=+30
```

## 5. Reallocate unattributed consumption

After the migration, the legacy consumption of users and accounts is held by an `(unattributed)` user and account,
visible in statistics. Run `slurm-quota-reallocate` on the controller node to attribute it to actual user/account pairs.
It can be run any time after the migration, quotas are enforced correctly meanwhile.

The command queries Slurm accounting (`sacct`) for jobs ending in the selected period, until the migration by default.
For CPU and GPU separately, it determines the actual user/account pairs and their shares from these jobs, distributes
unattributed consumption accordingly while preserving user and account totals, and removes the `(unattributed)` pairs,
user and account. The period does not need to cover all legacy consumption, it must be representative of how users
consumed on their accounts, for example the period since the last reset of consumptions.

Review the result first with `--dry-run`:

```bash
sudo slurm-quota-reallocate --starttime 2025-01-01 --dry-run
```

Then apply it:

```bash
sudo slurm-quota-reallocate --starttime 2025-01-01
```

Limits:

- Users and accounts without jobs in the selected period get their pairs from Slurm associations (`sacctmgr`) or, when
  none exists, from all counterparts with unattributed consumption. A warning is emitted for each user or account in the
  latter case.
- When version 3 adjustments targeted only users or only accounts, overall user and account consumptions differ. User
  totals are preserved and account totals are rescaled, with a warning for each account.
- GPU consumption shares are computed with the current GPU factors.
