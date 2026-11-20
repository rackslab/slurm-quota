# Application tokens

Application tokens give scripts and integrations, such as monitoring probes or reporting tools, access to the REST API
on behalf of a user. Compared to user tokens obtained with `slurm-quota login` or issued by `slurm-quota-token`, they:

- are valid longer, 90 days by default and up to 365 days,
- carry only the [scopes](../README.md#token-scopes) selected by their owner, among the scopes available for their role,
- can be listed and revoked before they expire,
- cannot be used to create, list or revoke application tokens.

## Server configuration

Validity durations of application tokens are defined in the `[jwt]` section of `/etc/slurm-quota/serve.ini`:

```ini
[jwt]
# Default validity of application tokens in days
app_duration=90
# Maximum validity users can request in days
app_max_duration=365
```

`app_max_duration` must be greater than or equal to `app_duration`. Application tokens are recorded in the `app_tokens`
table of the database, with their identifier, owner, name, scopes and dates. Tokens themselves are never stored.

## Creating tokens

Application tokens are created with a user token, for example saved with `slurm-quota login --save`. Select the scopes
granted to the token with `--scope`, repeated for each scope:

```bash
slurm-quota token create monitoring --scope stats:read:all
slurm-quota token create quotas-sync --scope quotas:read --scope quotas:write --duration 365
```

The token is printed on standard output, its identifier and expiration date on standard error, so it can be captured in
a variable or a file:

```bash
slurm-quota token create monitoring --scope stats:read:all > monitoring.token
```

> [!IMPORTANT]
> The token cannot be displayed again after its creation. Store it securely, it gives access to the REST API on your
> behalf until it expires or is revoked.

Token names must be unique among the active tokens of a user. Requesting a scope that is not available for the role of
the user, or a duration above `app_max_duration`, is refused.

Users can also create application tokens on the **API tokens** page of the web dashboard, when logged in with LDAP.

## Using tokens

Application tokens are used like user tokens, in the `Authorization` header of REST API requests:

```bash
curl -H "Authorization: Bearer $(cat monitoring.token)" https://controller:9911/stats
```

or with the `slurm-quota` command through the `SLURM_QUOTA_TOKEN` environment variable:

```bash
SLURM_QUOTA_TOKEN=$(cat monitoring.token) slurm-quota stats --all
```

Requests to routes outside the token scopes are refused with HTTP 403 `Token scope insufficient`. The role of the owner
is also checked on every request: when the owner loses a role, the token loses access to the routes of this role.

## Listing and revoking tokens

List your application tokens, with their identifier, scopes, dates and status (`active`, `expired` or `revoked`):

```console
$ slurm-quota token list
ID                                NAME         SCOPES                    CREATED           EXPIRES           STATUS
3f2a9c4e8b7d41c6a0e5d9b8c7f6e5d4  monitoring   stats:read:all            2026-09-02 10:15  2026-12-01 10:15  active
8c1d7e2f4a9b40e3b6d5c8a7f1e2d3c4  quotas-sync  quotas:read,quotas:write  2026-10-14 16:42  2027-10-14 16:42  active
b7e6d5c4a3f2419e8d7c6b5a4f3e2d1c  reporting    stats:read:own            2026-06-20 09:03  2026-09-18 09:03  expired
```

Dates are displayed in the local timezone. With `--user` or `--all`, a `USER` column shows the owner of each token.

Revoke a token with its identifier, it is refused immediately with HTTP 401 `Token is revoked`:

```bash
slurm-quota token revoke 3f2a9c4e8b7d41c6a0e5d9b8c7f6e5d4
```

Admins can list the application tokens of a user with `--user`, or of all users with `--all`, and revoke any token.

## REST API

| Route                 | Purpose                                                         |
| --------------------- | --------------------------------------------------------------- |
| `POST /tokens`        | Create an application token                                     |
| `GET /tokens`         | List own application tokens (`username` or `all` for **admin**) |
| `DELETE /tokens/<id>` | Revoke an application token (owner or **admin**)                |

These routes require a user token. For example:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name": "monitoring", "scopes": ["stats:read:all"], "duration": 180}' \
  https://controller:9911/tokens
```
