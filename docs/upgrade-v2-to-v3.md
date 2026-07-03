# Upgrade from version 2 to 3

This procedure applies to existing RPM-based version 2 installations. Version 3 keeps the same quota database path but
changes the Python packaging, command layout, REST API authentication, and web dashboard deployment paths.

For new installations, follow the main [Installation](../README.md#installation) guide instead.

## What changed

- RPM package upgrades are the normal upgrade path for existing v2 RPM deployments.
- The database migration is run automatically by the `slurm-quota-controller` RPM upgrade when
  `/var/lib/state/slurm-quota/slurm-quota.db` exists.
- `slurm-quota serve` is replaced by the `slurm-quota-serve` executable.
- `slurm-quota charge` is replaced by the `slurm-quota-charge` executable.
- `slurm-quota prune` is replaced by the `slurm-quota-prune` executable.
- `slurm-quota-token` is added for JWT token issuance.
- The CLI and web dashboard now authenticate against the REST API. With LDAP authentication, users typically run
  `slurm-quota login --save`.
- `slurm-quota-serve` reads site configuration from `/etc/slurm-quota/serve.ini` (authentication, authorization, JWT,
  and optional native HTTPS via `[tls]`).
- On compute, login, and web dashboard nodes, switch `SLURM_QUOTA_URL` from `http://` to `https://`; set
  `SLURM_QUOTA_CA_CERT` when the server certificate is not trusted by the system CA bundle.
- The REST API enforces a role-based authorization policy with four roles: `user`, `manager`, `operator`, and `admin`.
- By default, users only see their own stats.
- Administrative subcommands (quota changes, consumption adjustments, default quotas, GPU factors, and role management)
  require the `operator` or `admin` role.
- Users with the required role run administrative commands as themselves after authenticating; `sudo` is not required
  anymore.
- Web assets (`wsgi/`, `templates/`, `static/`) are installed under `/usr/share/slurm-quota/web/` instead of
  `/usr/share/slurm-quota-web/`.
- `/usr/libexec/slurm-quota/slurm-quota-web` is no longer a WSGI script. It is now the standalone console entry point,
  intended only for local testing with Flask's built-in HTTP server.
- Production Apache/mod_wsgi deployments must use the bundled WSGI script:
  `/usr/share/slurm-quota/web/wsgi/slurm-quota-web.wsgi`.
- RPM packages ship site configuration at `/etc/slurm-quota/serve.ini` and `/etc/default/slurm-quota-web`.

## 1. Backup the database

On the controller node, backup the SQLite database before upgrading packages:

```bash
sudo sqlite3 /var/lib/state/slurm-quota/slurm-quota.db ".backup /var/lib/state/slurm-quota/slurm-quota-v2-backup-$(date +%Y-%m-%d).db"
```

## 2. Upgrade RPM packages

On the controller node, upgrade the common and controller packages:

```bash
sudo dnf upgrade slurm-quota slurm-quota-controller
```

The `slurm-quota-controller` package upgrade automatically runs the database migration when the existing database file
is present.

To force or re-run the migration manually, use:

```bash
sudo /usr/libexec/slurm-quota/slurm-quota-migrate
```

On compute and login nodes, upgrade the common package:

```bash
sudo dnf upgrade slurm-quota
```

If the optional web dashboard is installed on a node, upgrade it too:

```bash
sudo dnf upgrade slurm-quota-web
```

RPM-managed systemd units, Slurm wrapper scripts, Lua plugin files, man pages, and web assets are refreshed by
`dnf upgrade`. If your site uses local custom copies instead of RPM-managed files, compare them with the upgraded files
before restarting services.

## 3. Configure the REST API

Version 3 requires REST API authentication for protected routes such as `GET /stats`. The `slurm-quota-controller`
package installs `/etc/slurm-quota/serve.ini`. Edit this file on the controller node.

For LDAP authentication, enable native HTTPS in production so tokens and LDAP credentials are not sent in cleartext over
the cluster network. Use at least:

```ini
[authentication]
method=ldap

[ldap]
uri=ldap://ldap.example.org
user_base=ou=people,dc=example,dc=org
group_base=ou=groups,dc=example,dc=org

[authorization]
admins=
  admin-user

[tls]
enabled=true
cert=/etc/slurm-quota/tls/cert.pem
key=/etc/slurm-quota/tls/key.pem
```

Install the server certificate and private key where the `slurm` user can read them (for example under
`/etc/slurm-quota/tls/`). Restrict the key to group `slurm` with mode `0640`. If these paths are outside directories
already listed in `ReadOnlyPaths=` in `slurm-quota.service`, extend that unit accordingly.

As an alternative to native TLS in `serve.ini`, you can terminate HTTPS at a reverse proxy in front of
`slurm-quota-serve` and leave the API on plain HTTP on a trusted local address (for example `http://127.0.0.1:9911/`).
Clients and the web dashboard then use the proxy's `https://` URL in `SLURM_QUOTA_URL`.

Replace the LDAP values and `admin-user` with site-specific values. The listed admins can manage roles and grant
operator or manager access through the CLI or web dashboard.

For JWT-only deployments, see [JWT authentication](authentication-jwt.md).

Restart the socket-activated API after changing the configuration:

```bash
sudo systemctl restart slurm-quota.socket
```

Check the API health endpoint on the controller:

```bash
curl https://127.0.0.1:9911/health
```

## 4. Update compute and login node environment

Update existing v2 `SLURM_QUOTA_URL=http://...` to `https://...` when native TLS or an HTTPS reverse proxy is enabled
(for example in `/etc/profile.d/slurm-quota.sh`):

```bash
export SLURM_QUOTA_URL=https://controller:9911/
```

When the API certificate is signed by a private CA that is not in the OS trust store, also set `SLURM_QUOTA_CA_CERT` on
every client node:

```bash
export SLURM_QUOTA_CA_CERT=/etc/slurm-quota/tls/ca.pem
```

Omit `SLURM_QUOTA_CA_CERT` when the server certificate is already trusted by the system CA bundle.

With LDAP authentication, users must obtain and save an API token before using the CLI. Verify authentication and access
from a compute or login node:

```bash
slurm-quota login --save
slurm-quota token
slurm-quota stats
```

By default, users only see their own stats.

Admins grant broader or administrative access through *manager* and *operator* roles. For example:

```bash
slurm-quota role grant operator bob    # manage quotas and view all stats
slurm-quota role grant manager carol   # view stats for assigned accounts only
slurm-quota role managers carol add hpc
slurm-quota role managers carol add research
```

## 5. Update the web dashboard

If the optional web dashboard is installed, create a session signing key for the v3 browser session:

```bash
sudo sh -c 'openssl rand -hex 32 > /etc/slurm-quota/web-session.key'
sudo chmod 0400 /etc/slurm-quota/web-session.key
sudo chown apache:apache /etc/slurm-quota/web-session.key
```

Edit `/etc/default/slurm-quota-web` installed by the `slurm-quota-web` package. Uncomment and set at least:

```bash
SLURM_QUOTA_URL=https://controller:9911/
SLURM_QUOTA_CA_CERT=/etc/slurm-quota/tls/ca.pem
SLURM_QUOTA_WEB_SESSION_KEY_FILE=/etc/slurm-quota/web-session.key
SLURM_QUOTA_WEB_SECURE_COOKIES=1
```

Omit `SLURM_QUOTA_CA_CERT` when the API certificate is already trusted by the system CA bundle on the web server host.

API TLS (`[tls]` in `serve.ini` or a reverse proxy) protects tokens and LDAP binds between the dashboard and
`slurm-quota-serve`. Apache HTTPS is still required for browser LDAP login: users submit their username and password
through the dashboard login form, so terminate HTTPS at Apache according to your site's normal procedure so these
credentials are not sent in cleartext over the network.

HTTP Basic authentication from the v2 example is no longer the primary authentication mechanism, though Apache-level
restrictions can still be added as an extra site policy if required.

Update the Apache virtual host. Replace the v2 paths:

```apache
WSGIScriptAlias / /usr/libexec/slurm-quota/slurm-quota-web
Alias /static/ /usr/share/slurm-quota-web/static/

<Directory /usr/share/slurm-quota-web/static>
    Require all granted
</Directory>

<Directory /usr/libexec/slurm-quota>
    Require all granted
</Directory>
```

with the v3 paths:

```apache
WSGIScriptAlias / /usr/share/slurm-quota/web/wsgi/slurm-quota-web.wsgi
Alias /static/ /usr/share/slurm-quota/web/static/

<Directory /usr/share/slurm-quota/web/static>
    Require all granted
</Directory>

<Directory /usr/share/slurm-quota/web/wsgi>
    <Files slurm-quota-web.wsgi>
        Require all granted
    </Files>
</Directory>
```

If the dashboard is mounted under a subdirectory, keep the static alias aligned with the `WSGIScriptAlias` mount point,
as in the main [Web dashboard](../README.md#web-dashboard) installation example.

Validate and reload Apache:

```bash
sudo apachectl configtest
sudo systemctl reload httpd
```

Open the dashboard in a browser and confirm that LDAP login works and the static assets load correctly.

## 6. Update scheduled jobs

Version 3 no longer exposes `prune` as a `slurm-quota` subcommand. If your site runs periodic database maintenance
through cron, systemd timers, or similar schedulers, review those entries on the controller node and replace
`slurm-quota prune` with `slurm-quota-prune`. For example:

```diff
- 0 3 * * 0 root /usr/bin/slurm-quota prune --preallocs
+ 0 3 * * 0 root /usr/bin/slurm-quota-prune --preallocs
```
