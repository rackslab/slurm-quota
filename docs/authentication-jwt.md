# JWT authentication

By default, slurm-quota is documented with [LDAP authentication](../README.md#controller-node) on the REST API. This guide covers the JWT-only alternative, where tokens are issued offline by an administrator instead of through `POST /login`.

## When to use JWT vs LDAP

| | LDAP (`method=ldap`) | JWT (`method=jwt`) |
|---|---|---|
| Token issuance | `POST /login` with LDAP credentials | `slurm-quota-token` as root |
| Web dashboard login | Browser login page | Service token (`SLURM_QUOTA_TOKEN`) |
| Typical use | Production clusters with existing LDAP | Small deployments, testing, service accounts |

## Server configuration

Copy and adapt `/etc/slurm-quota/serve.ini` (from `conf/serve.ini.example` or `/usr/share/slurm-quota/conf/serve.ini.example`):

```ini
[authentication]
method=jwt

[authorization]
admins=
  admin-user
```

The JWT signing key is created automatically at `/var/lib/slurm-quota/jwt.key` on first start (override in `[jwt]` if needed).

Restart the API service after changing `serve.ini`:

```bash
sudo systemctl restart slurm-quota.socket
```

## Issuing tokens

The `slurm-quota-token` command issues JWT tokens for `authentication.method=jwt` (root only):

```bash
sudo slurm-quota-token alice
sudo slurm-quota-token --duration 7 bob
```

## Client token storage

API clients must send a Bearer JWT header. The `slurm-quota stats` command reads a saved token from `$XDG_CONFIG_HOME/slurm-quota/token` (default `~/.config/slurm-quota/token`), or `SLURM_QUOTA_TOKEN` when set.

To persist a token from the environment:

```bash
export SLURM_QUOTA_TOKEN=$(sudo slurm-quota-token alice)
slurm-quota token              # persist env token for automatic use by stats
```

Set `SLURM_QUOTA_TOKEN` to override the saved token for a single command.

## Querying the API

```bash
TOKEN=$(sudo slurm-quota-token alice)
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:9911/stats
curl -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:9911/stats?username=alice"
curl -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:9911/stats?account=hpc"
```

## Web dashboard

> [!NOTE]
> With JWT authentication on the API, skip the session signing key file used for LDAP browser login.

Set `SLURM_QUOTA_TOKEN` in `/etc/default/slurm-quota-web` for service-account access without a login page:

```bash
sudo tee /etc/default/slurm-quota-web <<'EOF'
SLURM_QUOTA_URL=http://controller:9911/
SLURM_QUOTA_TOKEN=<token-from-slurm-quota-token>
EOF
sudo chmod 0644 /etc/default/slurm-quota-web
```

For standalone testing:

```bash
SLURM_QUOTA_URL=http://controller:9911/ SLURM_QUOTA_TOKEN=$(sudo slurm-quota-token alice) slurm-quota-web
```

When `SLURM_QUOTA_TOKEN` is set, the dashboard skips the browser login page.

## CLI reference

- `slurm-quota token`: saves the JWT from `SLURM_QUOTA_TOKEN` to the XDG config file (see [Usage](../README.md#slurm-quota-command) in the main README).
- `slurm-quota-token`: issues tokens (documented above).

> [!NOTE]
> For LDAP-based login and `slurm-quota login`, see the [Installation](../README.md#controller-node) and [Usage](../README.md#slurm-quota-command) sections in the main README.
