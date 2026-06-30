# Database migrations

> [!NOTE]
> When using RPM packages, migration is automatically run during `slurm-quota-controller` installation/upgrade (only when the existing database file is present).

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
