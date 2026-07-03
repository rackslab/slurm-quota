# Manual installation (from sources)

> [!NOTE]
> Use this guide when RPM packages are not suitable for your environment. For the recommended deployment path, see [Installation](../README.md#installation) in the main README.

## Controller node

Here is the procedure to follow to install the solution on the batch controller server:

1) Installation of Lua dependencies:

```bash
sudo dnf install lua-dbi lua-posix sqlite
```

2) Directories and permissions

```bash
# log directory for the wrapper
sudo mkdir -p /var/log/slurm/charge
sudo chown slurm: /var/log/slurm/charge

# data directory for the database
sudo mkdir -p /var/lib/state/slurm-quota
sudo chown slurm: /var/lib/state/slurm-quota
sudo chmod 0755 /var/lib/state/slurm-quota
```

3) Installation of the application

On the controller, include the `serve` extra for the HTTP JSON API (`slurm-quota-serve`):

```bash
sudo python3 -m pip install ".[serve]"
```

Optional: install Bash completion for `slurm-quota`:

```bash
sudo cp slurm-quota.bash-completion /etc/bash_completion.d/slurm-quota
sudo chmod 0644 /etc/bash_completion.d/slurm-quota
```

4) Configure REST API authentication and HTTPS

Authentication is required for `GET /stats`. Copy `conf/serve.ini.example` to
`/etc/slurm-quota/serve.ini` and adapt it to your site. See the
[Controller node](../README.md#controller-node) section in the main README for
the full recommended setup. RPM packages install this file directly at
`/etc/slurm-quota/serve.ini`; see the main [Installation](../README.md#installation)
guide. In production, enable native HTTPS on the API so tokens and LDAP
credentials are not sent in cleartext over the cluster network:

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

Install the server certificate and private key where the `slurm` user can read them (for example under `/etc/slurm-quota/tls/`). Restrict the key to group `slurm` with mode `0640`. If these paths are outside directories already listed in `ReadOnlyPaths=` in `slurm-quota.service`, extend that unit accordingly.

> [!NOTE]
> As an alternative to native TLS in `serve.ini`, you can terminate HTTPS at a reverse proxy (for example nginx, Apache, or stunnel) in front of `slurm-quota-serve` and leave the API on plain HTTP on a trusted local address (for example `http://127.0.0.1:9911/`). Clients and the web dashboard then use the proxy's `https://` URL in `SLURM_QUOTA_URL`.

With `method=ldap`, users obtain tokens through `slurm-quota login`; with `method=jwt`, tokens are issued by `slurm-quota-token` as root (see [JWT authentication](authentication-jwt.md)). Parameter reference is in `conf/serve.yml` in the source tree.

5) Installation of the HTTP JSON service (optional)

The HTTP JSON service allows exposing statistics via a REST API to facilitate integration with other tools. It is designed to work with systemd socket activation.

```bash
# Installation of systemd files
sudo cp slurm-quota.socket /etc/systemd/system/
sudo cp slurm-quota.service /etc/systemd/system/
sudo chmod 0644 /etc/systemd/system/slurm-quota.socket
sudo chmod 0644 /etc/systemd/system/slurm-quota.service

# Reload systemd and enable the service
sudo systemctl daemon-reload
sudo systemctl enable --now slurm-quota.socket

# Service verification
sudo systemctl status slurm-quota.socket
curl https://127.0.0.1:9911/health
```

Use `http://` instead when TLS is disabled or terminated at a reverse proxy on the controller. The service automatically stops after 10 minutes of inactivity (configurable via `--idle-timeout` in `slurm-quota.service`, with `0` meaning no idle timeout). Restart after changing `serve.ini`:

```bash
sudo systemctl restart slurm-quota.socket
```

6) Installation of the wrapper script

```bash
sudo cp slurm-quota-charge-wrapper /etc/slurm/slurm-quota-charge-wrapper
sudo chmod 0755 /etc/slurm/slurm-quota-charge-wrapper
```

7) Slurm submission plugin (`job_submit.lua`)

```bash
sudo cp job_submit.lua /etc/slurm/job_submit.lua
sudo chmod 0644 /etc/slurm/job_submit.lua
```

8) Activation of Slurm plugins

Edit the Slurm configuration to set up these parameters:

```ini
JobCompType=jobcomp/script
JobCompLoc=/etc/slurm/slurm-quota-charge-wrapper
JobSubmitPlugins=lua
AccountingStorageTRES=gres/gpu:<type1>,gres/gpu:<type2>
```

The `AccountingStorageTRES` parameter enables recording of complementary resource allocations (e.g., GPU, licenses) in addition to generic resources (e.g., nodes, cores, memory) in the Slurm accounting database. It is necessary to enable tracking of all GPU types in the cluster so that the `slurm-quota-charge` command can determine the GPUs allocated to completed jobs and account for the time consumed on these GPUs.

9) Logrotate configuration (recommended)

```bash
sudo cp slurm-quota-charge.logrotate /etc/logrotate.d/slurm-quota-charge
sudo chmod 0644 /etc/logrotate.d/slurm-quota-charge
```

> [!NOTE]
> It is recommended to back up the SQLite database file `/var/lib/state/slurm-quota/slurm-quota.db` regularly:

```bash
sudo sqlite3 /var/lib/state/slurm-quota/slurm-quota.db ".backup /var/lib/state/slurm-quota/slurm-quota-$(date +%Y-%m-%d).db"
```

## Other nodes

On the other nodes of the cluster, here are the steps to follow:

1) Installation of the application

```bash
sudo python3 -m pip install .
```

Optional: install Bash completion for `slurm-quota`:

```bash
sudo cp slurm-quota.bash-completion /etc/bash_completion.d/slurm-quota
sudo chmod 0644 /etc/bash_completion.d/slurm-quota
```

2) Set the `SLURM_QUOTA_URL` variable in the user environment

The `SLURM_QUOTA_URL` environment variable must point to the controller API. In production this is normally an `https://` URL. To facilitate use of the `slurm-quota stats` command, set it automatically for all users on every node, for example in `/etc/profile.d/slurm-quota.sh`:

```bash
export SLURM_QUOTA_URL=https://controller:9911/
```

When the API certificate is signed by a private CA or an in-house CA that is not in the OS trust store, install that CA on every client node and point the CLI at it with `SLURM_QUOTA_CA_CERT`:

```bash
export SLURM_QUOTA_URL=https://controller:9911/
export SLURM_QUOTA_CA_CERT=/etc/slurm-quota/tls/ca.pem
```

Omit `SLURM_QUOTA_CA_CERT` when the server certificate is issued by a public CA already trusted by the system (for example Let's Encrypt with the default system bundle).

When the controller uses LDAP authentication, run `slurm-quota login --save` once per user to store a token for `slurm-quota stats`.

## Web dashboard

1) Install the web dashboard:

```bash
sudo python3 -m pip install ".[web]"
```

2) Run standalone (HTTP built-in server, for testing only):

> [!NOTE]
> Export configuration variables in the shell; the standalone CLI does not read `/etc/default/slurm-quota-web`. Use plain `http://127.0.0.1:9911/` on the controller for quick local tests; production Apache deployments use the same `https://` API URL as compute and login nodes.

```bash
SLURM_QUOTA_URL=http://127.0.0.1:9911/ slurm-quota-web
```

When `authentication.method=ldap` and browser login is used, create a session key file and pass its path:

```bash
openssl rand -hex 32 > /tmp/web-session.key
chmod 0400 /tmp/web-session.key
SLURM_QUOTA_WEB_SESSION_KEY_FILE=/tmp/web-session.key SLURM_QUOTA_URL=http://127.0.0.1:9911/ slurm-quota-web
```

> [!NOTE]
> Alternatively, pass `SLURM_QUOTA_WEB_SESSION_KEY` directly in the shell for quick testing.
>
> Templates and static files are resolved automatically: repo-root `web/` when running from a git checkout, then the pip-installed data directory (for example `{prefix}/slurm-quota/web/`), then `/usr/share/slurm-quota/web`. If needed, set `SLURM_QUOTA_WEB_ASSETS_DIR` in the environment.

3) Configure Apache with this manual installation:

When LDAP browser login is used, create the session signing key file first:

```bash
sudo sh -c 'openssl rand -hex 32 > /etc/slurm-quota/web-session.key'
sudo chmod 0400 /etc/slurm-quota/web-session.key
sudo chown apache:apache /etc/slurm-quota/web-session.key
```

Create `/etc/default/slurm-quota-web` (loaded by `web/wsgi/slurm-quota-web.wsgi` before the application starts). Use the same `https://` API URL as on compute and login nodes. When LDAP browser login is used, set `SLURM_QUOTA_WEB_SESSION_KEY_FILE`. When the API uses a private CA, also set `SLURM_QUOTA_CA_CERT` so the dashboard can verify the backend certificate:

```bash
sudo tee /etc/default/slurm-quota-web <<'EOF'
SLURM_QUOTA_URL=https://controller:9911/
SLURM_QUOTA_CA_CERT=/etc/slurm-quota/tls/ca.pem
SLURM_QUOTA_WEB_SESSION_KEY_FILE=/etc/slurm-quota/web-session.key
SLURM_QUOTA_WEB_SECURE_COOKIES=1
EOF
sudo chmod 0644 /etc/default/slurm-quota-web
```

> [!NOTE]
> Omit `SLURM_QUOTA_CA_CERT` when the API certificate is already trusted by the system CA bundle on the web server host.
>
> A commented example with additional optional variables ships as `conf/slurm-quota-web.default` in the source tree, or `{prefix}/slurm-quota/conf/slurm-quota-web.default` after `pip install` (with `{prefix}` from `python3 -c "import sys; print(sys.prefix)"`).

Install Apache/mod_wsgi packages:

```bash
sudo dnf install httpd mod_wsgi httpd-tools
```

Use the bundled WSGI entry script (`web/wsgi/slurm-quota-web.wsgi` in a git checkout, or `{prefix}/slurm-quota/web/wsgi/slurm-quota-web.wsgi` after `pip install`, with `{prefix}` from `python3 -c "import sys; print(sys.prefix)"`):

```apache
<VirtualHost *:80>
    ServerName quota.example.org

    WSGIDaemonProcess slurm-quota-web processes=2 threads=5 display-name=%{GROUP}
    WSGIProcessGroup slurm-quota-web
    WSGIScriptAlias / /path/to/slurm-quota/web/wsgi/slurm-quota-web.wsgi
    # If you mount in a subdir (example: /quota), use:
    # WSGIScriptAlias /quota /path/to/slurm-quota/web/wsgi/slurm-quota-web.wsgi

    Alias /static/ /usr/local/slurm-quota/web/static/
    # Keep the static prefix aligned with WSGIScriptAlias:
    # - WSGIScriptAlias /      -> Alias /static/
    # - WSGIScriptAlias /quota -> Alias /quota/static/
    <Directory /usr/local/slurm-quota/web/static>
        Require all granted
    </Directory>

    <Directory /path/to/slurm-quota/web/wsgi>
        <Files slurm-quota-web.wsgi>
            Require all granted
        </Files>
    </Directory>

    ErrorLog /var/log/httpd/slurm-quota-web-error.log
    CustomLog /var/log/httpd/slurm-quota-web-access.log combined
</VirtualHost>
```

Enable and reload Apache as shown in the [Web dashboard](../README.md#web-dashboard) section of the main README.
