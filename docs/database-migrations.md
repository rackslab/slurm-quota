# Database migrations

> [!NOTE]
> When using RPM packages, migration is automatically run during `slurm-quota-controller` installation/upgrade (only
> when the existing database file is present).

To force migration manually with RPM packages, run:

```bash
sudo /usr/libexec/slurm-quota/slurm-quota-migrate
```

> [!NOTE]
> For manual/source-based deployments, run the database migration script before updating other components:

```bash
sudo slurm-quota-migrate
```

Example output:

```console
2025-12-04 10:11:42,926 - INFO - Adding array_size column to jobs_preallocations table
2025-12-04 10:11:42,938 - INFO - Migration completed: array_size column added
2025-12-04 10:11:42,939 - INFO - Database migration completed successfully
```

> [!NOTE]
> Then, the other components (`job_submit.lua`, `slurm-quota`, etc.) must be updated.

## Consumption per user/account pair (v4)

Starting with version 4, consumption is recorded per user/account pair, in seconds, in the `user_account_consumption`
table. The consumption of a user is the sum over all its accounts, and the consumption of an account is the sum over all
its users. Quotas remain defined at user and account levels, in minutes.

Version 3 databases only store consumption separately for each user and each account, in minutes. Which user consumed on
which account is not recorded, and the migration does not query Slurm. Legacy consumption is therefore stored on
temporary pairs with a special `(unattributed)` user and account:

- the legacy consumption of each user is stored on pair (user, `(unattributed)`),
- the legacy consumption of each account is stored on pair (`(unattributed)`, account).

User and account consumptions are preserved exactly, and quotas are enforced as before the migration. The
`(unattributed)` user and account are visible as regular entries in statistics until they are reallocated. The time of
the migration is recorded in the database.

Consumption columns are then removed from `users` and `accounts` tables. All these changes happen in a single
transaction: on any error, the database is left unchanged. The migration fails if a user or an account named
`(unattributed)` already exists.

> [!WARNING]
> This migration cannot be reverted. Backup the database before upgrading.

### Reallocate unattributed consumption

The `slurm-quota-reallocate` command then attributes this legacy consumption to actual user/account pairs, based on
Slurm accounting. It can be run any time after the migration, independently of it. It is described in
[upgrade from version 3 to 4](upgrade-v3-to-v4.md#5-reallocate-unattributed-consumption) and in
`slurm-quota-reallocate(1)` manpage.
