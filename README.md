# slurm-quota

## Objective

The objective of this solution is to assign CPU and GPU minute quotas to users and accounts on Slurm clusters, and to block Slurm job submissions and modifications when these quotas are reached.

![Screenshot of slurm-quota in action](assets/slurm-quota_screenshots.png)

The solution takes into account the time preallocated to jobs that are not yet completed. These jobs must be accounted for to prevent users/accounts from submitting jobs in parallel that, when added together, could exceed the quota once they are accounted for upon completion. By controlling the sum of "consumed + preallocated" at both the user and account levels, we ensure that reserved but not yet used capacity is properly accounted for and that the system is not over-committed.

## Architecture & Operation

The solution is built around a SQLite database located at `/var/lib/state/slurm-quota/slurm-quota.db`.

This database is used by 2 programs:

- `job_submit.lua`, a Lua script designed to be used as a Slurm submission plugin.
- `slurm-quota`, a Python application with several commands intended to be executed by Slurm, administrators, and cluster users.

When the Lua submission plugin is enabled in the Slurm configuration, the `job_submit.lua` script is automatically called during each job submission or modification (`sbatch`, `srun`, `scontrol`, etc.) to validate the request before it is accepted into the system. The script can thus apply custom rules (such as quota control for example) and reject jobs that do not comply with the defined policies.

The provided `job_submit.lua` script calculates the requested CPU minutes from `num_tasks × time_limit`. It also calculates the requested GPU minutes from the GPU resources specified in the job's TRES fields (`tres_per_job`, `tres_per_task`, `tres_per_node`, `tres_per_socket`), taking into account the load factors configured by GPU type. It then checks if a numeric quota is set in the database (`quota_cpu_minutes != -1` for CPU, `quota_gpu_minutes != -1` for GPU). The plugin compares the calculated CPU and GPU minute value for the job to the available share, defined as the quota minus the sum of "already consumed" and "already preallocated". This check is performed for both the user and the account, for both CPU and GPU. If the request exceeds any of the available shares (CPU or GPU), the submission or modification is refused, with an explicit message to the user.

When a job submission is accepted, the Lua script creates a corresponding preallocation in the `jobs_preallocations` table and associates this preallocation with a generated unique UUID identifier, the Slurm `username`, and the Slurm `account`. In case of an accepted job modification, the Lua script updates the preallocation assigned to the job in the database.

The Lua script generates a UUID at submission time to uniquely identify jobs and to be able to track the preallocated time until their completion. The job ID unfortunately cannot be used for this purpose because it is not yet available at the time of the `job_submit` callback (Slurm assigns a job ID later only if the job is accepted by the `job_submit.lua` script). The generated UUID identifier is stored in the job's `admin_comment` field, so it can be retrieved by the solution to reassociate the preallocation with the job during other steps.

The `slurm-quota-charge-wrapper` wrapper script is designed to be executed by Slurm's job completion _script_ plugin (`JobCompType=script`). When this functionality is enabled, Slurm executes this script every time a job completes or is cancelled. This wrapper script actually executes the `slurm-quota-charge` command. The wrapper is used for 2 reasons:

- Slurm does not allow directly specifying arguments to the command executed by the _script_ completion plugin. The wrapper allows this limitation to be bypassed.
- Slurm systematically redirects JobComp script output to `/dev/null`. By using the wrapper as an intermediate layer, it is possible to redirect the output of the `slurm-quota-charge` command to a dedicated log file (`/var/log/slurm/charge/slurm-quota-charge.log`) to ensure that all processing information and any errors are preserved to trace operations.

Upon job completion, the `slurm-quota-charge` command retrieves the UUID from `admin_comment` and the allocated GPU resources from `AllocTRES` (via `sacct`). It calculates the effective consumption in CPU minutes according to `PROCS × (END − START) / 60` and in GPU minutes according to the allocated GPUs, their type, and the configured load factors. It credits these consumptions to the user and to the account, and deletes the corresponding preallocation in the database. This step ensures that the difference between "reserved" and "actually used" is correctly reconciled for both dimensions (CPU and GPU).

In the SQLite database, there are 4 tables:

- `users`: It contains user names, consumed CPU and GPU minutes, and assigned quotas.
- `accounts`: It contains Slurm account names, consumed CPU and GPU minutes, and assigned quotas.
- `jobs_preallocations`: It contains CPU and GPU minutes preallocated to non-completed jobs, with `job_uuid`, `username`, and `account`.
- `gpu_factors`: It contains load factors by GPU type, allowing calculation of billed GPU minutes based on the GPU type used.

We record the amount of preallocated time per job rather than a global value per user to allow fine-grained updates during modifications (increase or decrease of `time_limit`/`num_tasks`), targeted deletion of the preallocation upon completion, and robust cleanup of orphans. A global value would hide the detail per job and significantly complicate adjustments and cancellations, with an increased risk of inconsistencies.

The SQLite database file must have the system user `slurm` as owner, with mode `0644` to restrict modification permission to the `slurm` user (used by `slurmctld` for the Lua script, the jobcomp script, and `slurm-quota-serve`) and to administrators with the `root` account. Other users only have read-only access to the database. `slurm-quota-charge` and `slurm-quota-serve` automatically create the database when it is missing (on first charge run or HTTP API service start), setting the correct permissions on the file.

The commands `slurm-quota user-quota`, `slurm-quota account-quota`, `slurm-quota user-gpu-quota`, and `slurm-quota account-gpu-quota` respectively allow assigning CPU and GPU quotas to users and accounts through the REST API (manager or admin role required; see below).
The `slurm-quota adjust` command allows manually adjusting consumed CPU/GPU time for one user or account with an explicitly signed delta through the REST API (manager or admin role required; see below).

Default quotas used when the solution auto-creates a user or account can be displayed with `slurm-quota default-quotas` and updated with `slurm-quota set-default-quotas` through the REST API (manager or admin role required; see below). These defaults are applied only to newly auto-created entries and do not modify existing users/accounts.

The solution allows setting GPU load factors. This is a multiplicative coefficient applied to the calculation of consumed GPU minutes based on the GPU type used. This factor allows adjusting, for each GPU type, the actual consumption weighting, taking into account the different value or computing power of the models (for example, assigning a factor of 0.5 to an h100 GPU amounts to counting 10 minutes of usage as only 5 minutes consumed). The default factor is 1.0 if no specific factor is configured for a given type. Thus, administrators can finely adapt GPU billing based on GPU models.

The `slurm-quota set-gpu-factor` command allows configuring load factors by GPU type (restricted to root). The `slurm-quota gpu-factors` command displays the currently configured GPU load factors.

The `slurm-quota-serve` command starts a small HTTP/JSON server designed to work with systemd "socket activation". It must run as the `slurm` system user and creates the SQLite database on first start if it does not exist yet. A `slurm-quota.socket` socket unit listens on TCP port 9911 and launches the `slurm-quota.service` service on demand upon the first connection. The server can automatically stops after a configurable period of inactivity (10 minutes by default). The API exposes `GET /health` for liveness probes and `GET /stats`, which returns a JSON object of the form `{ users: [...], accounts: [...] }`. Optional query parameters can be used to filter responses: `username` filters users and limits accounts to this user's Slurm associations (e.g. `/stats?username=alice`), while `account` returns only the requested account stats in the `accounts` array (e.g. `/stats?account=hpc`).

REST API authentication is required: `GET /stats` always requires a valid Bearer JWT. With `authentication.method=ldap` in site configuration, `POST /login` issues JWT tokens from LDAP credentials. With `authentication.method=jwt`, tokens are issued offline by the root-only `slurm-quota-token` command.

REST API authorization uses three roles. **Admin** users are listed in `[authorization] admins` in `serve.ini` (bootstrap only; not stored in the database). **Manager** users are stored in the SQLite `api_managers` table and can view all statistics. All other authenticated users have the **user** role and can view only their own consumption stats (including Slurm accounts they belong to). Admins can grant or revoke the manager role through `GET /roles`, `PUT /roles/managers/<username>`, and `DELETE /roles/managers/<username>`. The `slurm-quota role` CLI subcommands and the web dashboard **Manage roles** page (admin only) call these endpoints. Managers and admins can set user and account CPU/GPU quotas through `PUT /quotas/users/<username>/cpu`, `PUT /quotas/users/<username>/gpu`, `PUT /quotas/accounts/<account>/cpu`, and `PUT /quotas/accounts/<account>/gpu` (JSON body: `{"quota_minutes": <int>}`, where `-1` means unlimited). The `slurm-quota user-quota`, `account-quota`, `user-gpu-quota`, and `account-gpu-quota` CLI subcommands and the web dashboard quota edit forms call these endpoints. Managers and admins can view and update default quotas applied to newly auto-created users/accounts through `GET /quotas/defaults` and `PUT /quotas/defaults` (JSON body: partial object with any of `user_cpu_minutes`, `user_gpu_minutes`, `account_cpu_minutes`, `account_gpu_minutes`; `-1` means unlimited). The `slurm-quota default-quotas` and `set-default-quotas` CLI subcommands call these endpoints. Managers and admins can view and update GPU charging factors through `GET /factors/gpu` (response: `{"default_factor": <float>, "factors": {<gpu_type>: <float>, ...}}`) and `PUT /factors/gpu/<gpu_type>` (JSON body: `{"factor": <positive float>}`). The `slurm-quota gpu-factors` and `set-gpu-factor` CLI subcommands call these endpoints. Managers and admins can adjust consumed CPU/GPU time through `PATCH /consumption/user/<username>/cpu`, `PATCH /consumption/user/<username>/gpu`, `PATCH /consumption/account/<account>/cpu`, and `PATCH /consumption/account/<account>/gpu` (JSON body: `{"delta_minutes": <signed int>}`; response: `{"total_consumed_minutes": <int>}`). The `slurm-quota adjust` CLI subcommand and the web dashboard consumption edit forms call these endpoints.

The `slurm-quota stats` command consumes this HTTP/JSON API by default (URL configurable via the `SLURM_QUOTA_URL` environment variable, default `http://127.0.0.1:9911/`). It queries `/stats` and displays a readable table in the terminal. By specifying a user (`--user` or positional username), an account (`--account`), or the `--all` option, the command transmits the appropriate filters to the service. User and account selectors are mutually exclusive. Users with the **user** role receive `HTTP 403` when requesting data outside their scope. If the service is not available or in case of connection failure to the server, execution fails with an error message.

`slurm-quota stats` needs a JWT token sent as a Bearer header. It reads a saved token from `$XDG_CONFIG_HOME/slurm-quota/token` (default `~/.config/slurm-quota/token`), or `SLURM_QUOTA_TOKEN` when set. When `authentication.method=ldap`, `slurm-quota login` obtains a JWT from `POST /login`; by default it prints the token to stdout, and with `--save` persists it for automatic use by `stats`. When tokens are issued with `slurm-quota-token`, set `SLURM_QUOTA_TOKEN` or run `slurm-quota token` to save the token to the same file.

Additionally, a logrotate configuration file is provided (`slurm-quota-charge.logrotate`) to prevent the log file fed by the `slurm-quota-charge-wrapper` wrapper from growing too large over time.

The `slurm-quota-web` application is a web dashboard that retrieves the same statistics from the HTTP API (`GET /stats`) and renders them as HTML tables and quota usage bars. When `authentication.method=ldap`, the dashboard presents a login page and stores each user's API JWT in a signed, HttpOnly session cookie. Alternatively, set `SLURM_QUOTA_TOKEN` in the web server environment to authenticate API calls with a service token (no per-user login). Users with the **user** role see only their own stats; managers and admins see all data. Managers and admins can edit quotas and adjust consumption inline on the dashboard. Admins can open **Manage roles** to list all users with their role and grant or revoke manager access. It can run standalone with Flask built-in HTTP server for local testing, or be launched by a production-ready HTTP server (for example Apache with mod_wsgi) as a WSGI application.

## Installation

### RPM packages (recommended)

RPM packages are published for **Enterprise Linux 9** (RHEL 9, Rocky Linux 9, AlmaLinux 9, CentOS Stream 9, and similar) in the [Rackslab packages](https://pkgs.rackslab.io/rpm/) repository.

1) Install the Rackslab repository keyring:

```bash
sudo curl https://pkgs.rackslab.io/keyring.asc --output /etc/pki/rpm-gpg/RPM-GPG-KEY-Rackslab
```

2) Create `/etc/yum.repos.d/rackslab.repo` with this content:

```ini
[rackslab]
name=Rackslab
baseurl=https://pkgs.rackslab.io/rpm/el9/main/$basearch/
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-Rackslab
```

The following packages are available:

- `slurm-quota`: common files for all nodes (CLI, manpage, bash completion)
- `slurm-quota-controller`: controller-only files (`job_submit.lua`, wrapper, systemd units, logrotate, migration script)
- `slurm-quota-web`: optional web application with HTML dashboard

#### Controller node

1) Install controller and common packages:

```bash
sudo dnf install slurm-quota slurm-quota-controller
```

2) Start and enable the socket-activated API service:

```bash
sudo systemctl enable --now slurm-quota.socket
```

3) Configure Slurm plugins in `slurm.conf`:

Edit the Slurm configuration to set up these parameters:

```ini
JobCompType=jobcomp/script
JobCompLoc=/etc/slurm/slurm-quota-charge-wrapper
JobSubmitPlugins=lua
AccountingStorageTRES=gres/gpu:<type1>,gres/gpu:<type2>
```

The `AccountingStorageTRES` parameter enables recording of complementary resource allocations (e.g., GPU, licenses) in addition to generic resources (e.g., nodes, cores, memory) in the Slurm accounting database. It is necessary to enable tracking of all GPU types in the cluster so that the `slurm-quota-charge` command can determine the GPUs allocated to completed jobs and account for the time consumed on these GPUs.

4) Configure REST API authentication

Authentication is required for `GET /stats`. Copy and adapt `/etc/slurm-quota/serve.ini` (from `conf/serve.ini.example` or `/usr/share/slurm-quota/conf/serve.ini.example`):

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
```

With `method=ldap`, the service exposes `POST /login` and issues JWT tokens after LDAP bind. The JWT signing key is created automatically at `/var/lib/slurm-quota/jwt.key` on first start (override in `[jwt]` if needed). Additional LDAP options (`bind_dn`, `restricted_groups`, TLS, and so on) are documented in `/usr/share/slurm-quota/conf/serve.yml`.

List bootstrap admin usernames under `[authorization] admins`. These users can list all roles and grant or revoke manager access.

Restart the API service after changing `serve.ini`:

```bash
sudo systemctl restart slurm-quota.socket
```

> [!NOTE]
> **Alternative: JWT-only authentication.** Without LDAP, set `authentication.method=jwt` in `serve.ini` and issue tokens as root with `slurm-quota-token`. Clients use `SLURM_QUOTA_TOKEN` or `slurm-quota token` to configure `stats` and the web dashboard.

#### Compute and login nodes

1) Install the common package:

```bash
sudo dnf install slurm-quota
```

2) Configure the controller API endpoint for all users in `/etc/profile.d/slurm-quota.sh`:

```bash
export SLURM_QUOTA_URL=http://controller:9911/
```

3) Obtain an API token (when `authentication.method=ldap`):

```bash
slurm-quota login --save
```

#### Web dashboard

1) Install the web dashboard package on the node running Apache:

```bash
sudo dnf install slurm-quota-web
```

2) Install Apache/mod_wsgi packages:

```bash
sudo dnf install httpd mod_wsgi httpd-tools
```

3) Create the session signing key file (skip when using `authentication.method=jwt`):

When LDAP authentication is used on the REST API (`/etc/slurm-quota/serve.ini`), store a session key in `/etc/slurm-quota/web-session.key`. This directory is also used by `slurm-quota-serve` for `serve.ini` and is typically already present:

```bash
sudo sh -c 'openssl rand -hex 32 > /etc/slurm-quota/web-session.key'
sudo chmod 0400 /etc/slurm-quota/web-session.key
sudo chown apache:apache /etc/slurm-quota/web-session.key
```

> [!NOTE]
> As an alternative for quick testing, you can set the key directly with `SLURM_QUOTA_WEB_SESSION_KEY` (for example `SetEnv SLURM_QUOTA_WEB_SESSION_KEY <random-key>`). This keeps the key in the web server configuration and is less suitable for production.

4) Configure Apache virtual host with mod_wsgi:

```apache
<VirtualHost *:80>
    ServerName quota.example.org

    # Optional: point web app to remote API endpoint
    SetEnv SLURM_QUOTA_URL http://127.0.0.1:9911/

    # Required when REST API LDAP authentication is enabled:
    SetEnv SLURM_QUOTA_WEB_SESSION_KEY_FILE /etc/slurm-quota/web-session.key
    # Recommended behind HTTPS:
    # SetEnv SLURM_QUOTA_WEB_SECURE_COOKIES 1

    WSGIDaemonProcess slurm-quota-web processes=2 threads=5 display-name=%{GROUP}
    WSGIProcessGroup slurm-quota-web
    WSGIScriptAlias / /usr/share/slurm-quota/web/wsgi/slurm-quota-web.wsgi
    # If you mount in a subdir (example: /quota), use:
    # WSGIScriptAlias /quota /usr/share/slurm-quota/web/wsgi/slurm-quota-web.wsgi

    Alias /static/ /usr/share/slurm-quota/web/static/
    # Keep the static prefix aligned with WSGIScriptAlias:
    # - WSGIScriptAlias /      -> Alias /static/
    # - WSGIScriptAlias /quota -> Alias /quota/static/
    <Directory /usr/share/slurm-quota/web/static>
        Require all granted
    </Directory>

    <Directory /usr/share/slurm-quota/web/wsgi>
        <Files slurm-quota-web.wsgi>
            Require all granted
        </Files>
    </Directory>

    ErrorLog /var/log/httpd/slurm-quota-web-error.log
    CustomLog /var/log/httpd/slurm-quota-web-access.log combined
</VirtualHost>
```

5) Enable and reload Apache:

```bash
sudo systemctl enable --now httpd
sudo apachectl configtest
sudo systemctl reload httpd
```

When `authentication.method=ldap`, users sign in through the web dashboard with their LDAP credentials. Alternatively, set `SLURM_QUOTA_TOKEN` in the web server environment for service-account access without a login page. Behind HTTPS, also set `SLURM_QUOTA_WEB_SECURE_COOKIES=1` so cookies are marked `Secure`. Session lifetime defaults to one day (`SLURM_QUOTA_WEB_SESSION_DAYS`) to match the default JWT duration.

Security recommendations:

- Keep the backend API bound to cluster local networks when possible.
- Restrict dashboard access to trusted networks when possible.
- Prefer HTTPS/TLS at Apache level and enable `SLURM_QUOTA_WEB_SECURE_COOKIES` when LDAP web login is used.

### Manual installation (from sources)

#### Controller Node

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

4) Configure REST API authentication

Authentication is required for `GET /stats`. Copy and adapt `/etc/slurm-quota/serve.ini` (see the [Controller node](#controller-node) RPM section for the recommended LDAP setup). With `method=ldap`, users obtain tokens through `slurm-quota login`; with `method=jwt`, tokens are issued by `slurm-quota-token` as root.

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
curl http://127.0.0.1:9911/health
```

The service automatically stops after 10 minutes of inactivity (configurable via `--idle-timeout` in `slurm-quota.service`, with `0` meaning no idle timeout).

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

It is recommended to back up the SQLite database file `/var/lib/state/slurm-quota/slurm-quota.db`. To do this, simply run this command regularly:

```bash
sudo sqlite3 /var/lib/state/slurm-quota/slurm-quota.db ".backup /var/lib/state/slurm-quota/slurm-quota-$(date +%Y-%m-%d).db"
```

#### Other Nodes

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

The `SLURM_QUOTA_URL` environment variable must point to the controller node to indicate the server to query to obtain quotas. To facilitate the use of the `slurm-quota stats` command, this variable must be automatically set in the user environment on all nodes. For example, it is possible to add the following line in the `/etc/profile.d/sh.local` file:

```bash
export SLURM_QUOTA_URL=http://controller:9911/
```

When the controller uses LDAP authentication, run `slurm-quota login --save` once per user to store a token for `slurm-quota stats`.

#### Web dashboard (optional)

1) Install the web dashboard:

```bash
sudo python3 -m pip install ".[web]"
```

2) Run standalone (HTTP built-in server, for testing only):

```bash
SLURM_QUOTA_URL=http://127.0.0.1:9911/ slurm-quota-web
```

When `authentication.method=ldap` and browser login is used, also configure a session signing key.

> [!TIP]
> Prefer a key file referenced by `SLURM_QUOTA_WEB_SESSION_KEY_FILE`:
>
> ```bash
> openssl rand -hex 32 > /tmp/web-session.key
> chmod 0400 /tmp/web-session.key
> SLURM_QUOTA_WEB_SESSION_KEY_FILE=/tmp/web-session.key SLURM_QUOTA_URL=http://127.0.0.1:9911/ slurm-quota-web
> ```

> [!NOTE]
> As an alternative, pass the key directly for quick testing:
>
> ```bash
> SLURM_QUOTA_WEB_SESSION_KEY=$(openssl rand -hex 32) slurm-quota-web
> ```

> [!NOTE]
> Templates and static files are resolved automatically: repo-root `web/` when running from a git checkout, then the pip-installed data directory (for example `{prefix}/slurm-quota/web/`), then `/usr/share/slurm-quota/web`. If needed, set `SLURM_QUOTA_WEB_ASSETS_DIR` environment variable to use a custom directory containing `templates/` and `static/` (for example in Apache: `SetEnv SLURM_QUOTA_WEB_ASSETS_DIR /path/to/assets`).

3) Create the session signing key file when using LDAP browser login (`authentication.method=ldap`):

When LDAP authentication is used on the REST API (`/etc/slurm-quota/serve.ini`), store a session key in `/etc/slurm-quota/web-session.key`. This directory is also used by `slurm-quota-serve` for `serve.ini` and is typically already present:

```bash
sudo sh -c 'openssl rand -hex 32 > /etc/slurm-quota/web-session.key'
sudo chmod 0400 /etc/slurm-quota/web-session.key
sudo chown apache:apache /etc/slurm-quota/web-session.key
```

> [!NOTE]
> As an alternative for quick testing, you can set the key directly with `SLURM_QUOTA_WEB_SESSION_KEY` (for example `SetEnv SLURM_QUOTA_WEB_SESSION_KEY <random-key>`). This keeps the key in the web server configuration and is less suitable for production.

4) Configure Apache with this manual installation:

Install Apache/mod_wsgi packages:

```bash
sudo dnf install httpd mod_wsgi httpd-tools
```

Use the bundled WSGI entry script (`web/wsgi/slurm-quota-web.wsgi` in a git checkout, or `{prefix}/slurm-quota/web/wsgi/slurm-quota-web.wsgi` after `pip install`, with `{prefix}` from `python3 -c "import sys; print(sys.prefix)"`):

```apache
<VirtualHost *:80>
    ServerName quota.example.org

    SetEnv SLURM_QUOTA_URL http://127.0.0.1:9911/

    # Required when REST API LDAP authentication is enabled:
    SetEnv SLURM_QUOTA_WEB_SESSION_KEY_FILE /etc/slurm-quota/web-session.key
    # SetEnv SLURM_QUOTA_WEB_SECURE_COOKIES 1

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

Enable and reload Apache as shown in the RPM installation section. When using LDAP browser login or per-user sessions, include `SLURM_QUOTA_WEB_SESSION_KEY_FILE` (or `SLURM_QUOTA_WEB_SESSION_KEY` as an alternative) and `SLURM_QUOTA_WEB_SECURE_COOKIES` behind HTTPS in the virtual host as described above. For service-token access, set `SLURM_QUOTA_TOKEN` instead.

## Usage

### `slurm-quota` Command

- `login`: Obtains a JWT from `POST /login` when `authentication.method=ldap`.

  ```bash
  slurm-quota login              # prompts for password, prints token to stdout
  slurm-quota login bob          # same for LDAP user bob
  slurm-quota login --save       # save token for automatic use by stats
  ```

  Use `--save` to store the token in `$XDG_CONFIG_HOME/slurm-quota/token` (default `~/.config/slurm-quota/token`).

- `token`: Saves the JWT from `SLURM_QUOTA_TOKEN` to the XDG config file.

  ```bash
  export SLURM_QUOTA_TOKEN=$(sudo slurm-quota-token alice)
  slurm-quota token              # persist env token for automatic use by stats
  ```

  Set `SLURM_QUOTA_TOKEN` to override the saved token for a single command.

- `stats`: Displays consumed CPU times, preallocated CPU times (with the number of jobs considered), and quotas for users and accounts.

Examples:

```bash
slurm-quota stats                 # displays the current user and their accounts
slurm-quota stats alice           # details for user alice and their accounts
slurm-quota stats --user alice    # same as positional username
slurm-quota stats --account hpc   # only stats for account hpc
slurm-quota stats --all           # lists all users and all accounts
slurm-quota stats --hours         # same stats displayed in hours
```

Note: `--account` is mutually exclusive with user selection (`--user` or positional username).

Color display of the status bar can be disabled by setting the `NO_COLOR` environment variable.
The `--hours` option changes only the displayed unit in the `stats` output; stored values and API values remain in minutes.

- `role`: Show or manage REST API roles (requires a saved token or `SLURM_QUOTA_TOKEN`).

Examples:

```bash
slurm-quota role show            # show current user and role (GET /me)
slurm-quota role list            # list all users with roles (admin only)
slurm-quota role grant bob       # grant manager role (admin only)
slurm-quota role revoke bob      # revoke manager role (admin only)
```

- `user-quota` (requires manager or admin role): Sets a CPU quota for a user.

Examples:

```bash
slurm-quota user-quota alice 50000            # 50k CPU minutes
slurm-quota user-quota bob -1                 # unlimited
```

- `user-gpu-quota` (requires manager or admin role): Sets a GPU quota for a user.

Examples:

```bash
slurm-quota user-gpu-quota alice 10000        # 10k GPU minutes
slurm-quota user-gpu-quota bob -1             # unlimited GPU
```

- `account-quota` (requires manager or admin role): Sets a CPU quota for a Slurm account.

Examples:

```bash
slurm-quota account-quota projX 200000        # 200k CPU minutes for account projX
slurm-quota account-quota projY -1            # unlimited
```

- `account-gpu-quota` (requires manager or admin API role): Sets a GPU quota for a Slurm account.

Examples:

```bash
slurm-quota account-gpu-quota projX 50000   # 50k GPU minutes
slurm-quota account-gpu-quota projY -1      # unlimited GPU
```

- `adjust`: Adjusts consumed CPU/GPU time for one user or one account (manager or admin role required).

Examples:

```bash
slurm-quota adjust --user alice --cpu --minutes=+30     # add 30 consumed CPU minutes
slurm-quota adjust --user alice --gpu --minutes=-120    # subtract 120 consumed GPU minutes
slurm-quota adjust --account projX --cpu --hours=+2     # add 2 consumed CPU hours (120 minutes)
slurm-quota adjust --account projX --gpu --hours=-1     # subtract 1 consumed GPU hour (60 minutes)
```

Notes:
- The delta must be explicitly signed (`+` or `-`), for example `+30` or `-30`.
- Subtractions are clamped to zero: consumed time never becomes negative.

- `default-quotas` (manager or admin role required): Displays the default CPU/GPU quotas applied to newly auto-created users/accounts.

Example:

```bash
slurm-quota default-quotas
```

- `set-default-quotas` (manager or admin role required): Sets one or more default quotas applied when a user/account is auto-created by the submission plugin. Existing users/accounts are not modified.

Examples:

```bash
slurm-quota set-default-quotas --user-cpu 50000 --account-cpu 200000
slurm-quota set-default-quotas --user-gpu 10000 --account-gpu 50000
slurm-quota set-default-quotas --user-cpu -1 --user-gpu -1 --account-cpu -1 --account-gpu -1
```

- `gpu-factors`: Displays the currently configured GPU load factors (manager or admin role).

Example:

```bash
slurm-quota gpu-factors
```

- `set-gpu-factor`: Configures the load factor for a GPU type through the REST API (manager or admin role required). Billed GPU minutes are calculated as `number_GPU × time_minutes × factor`. The default factor is 1.0 if no factor is configured for a GPU type. Argument _factor_ must be a positive float (> 0).

Examples:

```bash
slurm-quota set-gpu-factor h100 0.5    # Factor 0.5 for h100 GPUs
slurm-quota set-gpu-factor h200 0.8    # Factor 0.8 for h200 GPUs
slurm-quota set-gpu-factor default 1.0  # Default factor (used if type is not specified)
```


### `slurm-quota-prune` Command

Cleans orphaned or unused data from the database (root only). Dedicated selectors:

- `--preallocs`: remove orphaned preallocations (jobs not present in Slurm queue)
- `--users`: remove users with both consumed CPU and consumed GPU at 0
- `--accounts`: remove accounts with both consumed CPU and consumed GPU at 0
- `--all`: prune all categories above (default behavior when no selector is provided)
- `--user <username>`: limit user pruning candidates to one username
- `--account <account>`: limit account pruning candidates to one account
- `--dry-run`: report how many preallocations/users/accounts would be removed, without deleting rows

Examples:

```bash
sudo slurm-quota-prune                  # default: same as --all
sudo slurm-quota-prune --preallocs      # prune only orphaned preallocations
sudo slurm-quota-prune --users          # prune only users with 0 consumed CPU/GPU
sudo slurm-quota-prune --users --user alice      # prune only this eligible user
sudo slurm-quota-prune --accounts       # prune only accounts with 0 consumed CPU/GPU
sudo slurm-quota-prune --accounts --account hpc  # prune only this eligible account
sudo slurm-quota-prune --dry-run        # preview removals without applying them
```

It is normally not necessary to execute this command under normal conditions. It may be useful in case of malfunction of the call to the `slurm-quota-charge` command by Slurm. Its execution is nevertheless safe, it can be executed if in doubt about the preallocated durations assigned to users.


### `slurm-quota-serve` Command

Launches an HTTP REST API JSON server with `GET /health`, `GET /stats` (JWT required), and `POST /login` when `authentication.method=ldap`. Designed to work with systemd socket activation.

Examples:

```bash
# Manual launch (testing; must run as slurm user)
sudo -u slurm slurm-quota-serve --host 127.0.0.1 --port 9911 --idle-timeout 600
sudo -u slurm slurm-quota-serve --host 127.0.0.1 --port 9911 --idle-timeout 0    # no idle timeout

# Via systemd (recommended)
sudo systemctl start slurm-quota.socket
curl http://127.0.0.1:9911/health
TOKEN=$(sudo slurm-quota-token alice)   # when authentication.method=jwt
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:9911/stats
curl -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:9911/stats?username=alice"
curl -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:9911/stats?account=hpc"
```

The service automatically stops after a period of inactivity (600 seconds, ie. 10 minutes by default). This can be disabled with `--idle-timeout 0` argument. The `stats` command queries this HTTP service (URL configurable via the `SLURM_QUOTA_URL` environment variable).

Dump resolved configuration (passwords masked) and exit:

```bash
slurm-quota-serve --dump-config
```

LDAP login when `authentication.method=ldap` (setup described in the [Controller node](#controller-node) installation section):

```bash
slurm-quota login --save
```

Or with `curl`:

```bash
curl -s -X POST http://127.0.0.1:9911/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"secret"}'
```

After `slurm-quota login --save`, `slurm-quota stats` automatically uses the saved token.

Query `/stats` with a token:

```bash
TOKEN=$(slurm-quota login)              # ldap method
TOKEN=$(sudo slurm-quota-token alice)   # jwt method
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:9911/stats
```

### `slurm-quota-token` Command

Issues JWT tokens for `authentication.method=jwt` (root only).

```bash
sudo slurm-quota-token alice
sudo slurm-quota-token --duration 7 bob
```

### `slurm-quota-web` Command

Launches the web dashboard.

Environment variables:

- `SLURM_QUOTA_URL`: base URL of the HTTP API (default `http://127.0.0.1:9911/`)
- `SLURM_QUOTA_TOKEN`: service JWT for API calls; when set, the dashboard skips the browser login page; when not set, unauthenticated requests are redirected to the login form for LDAP authentication.
- `SLURM_QUOTA_WEB_SESSION_KEY_FILE`: path to a file containing the session signing key (required for LDAP authentication).
- `SLURM_QUOTA_WEB_SESSION_KEY`: session signing key passed directly (alternative to `SLURM_QUOTA_WEB_SESSION_KEY_FILE`)
- `SLURM_QUOTA_WEB_SECURE_COOKIES`: set to `1` to mark session cookies `Secure` (recommended behind HTTPS when using LDAP browser login)
- `SLURM_QUOTA_WEB_SESSION_DAYS`: browser session lifetime in days (default `1`)
- `SLURM_QUOTA_WEB_HOST`, `SLURM_QUOTA_WEB_PORT`, `SLURM_QUOTA_WEB_DEBUG`: standalone server options

Examples:

```bash
slurm-quota-web
SLURM_QUOTA_WEB_HOST=0.0.0.0 SLURM_QUOTA_WEB_PORT=8080 slurm-quota-web
SLURM_QUOTA_URL=http://controller:9911/ SLURM_QUOTA_TOKEN=$(sudo slurm-quota-token alice) slurm-quota-web
```

## Upgrade

### Migrate from manual installation to RPM packages

Use this procedure to switch an existing manual deployment to RPM-managed files.

1) Back up the database on the controller:

```bash
sudo sqlite3 /var/lib/state/slurm-quota/slurm-quota.db ".backup /var/lib/state/slurm-quota/slurm-quota-pre-rpm-$(date +%Y-%m-%d).db"
```

2) Remove legacy manually installed files that conflict with RPM-managed paths:

On the controller node:

```bash
# Legacy systemd unit locations used by manual installation
sudo rm -f /etc/systemd/system/slurm-quota.service
sudo rm -f /etc/systemd/system/slurm-quota.socket
```

On all nodes:

```bash
# Legacy manual binary/completion/manpage copies (RPM will reinstall managed files)
sudo rm -f /usr/local/bin/slurm-quota
sudo rm -f /usr/local/bin/slurm-quota-charge
sudo rm -f /usr/local/bin/slurm-quota-prune
sudo rm -f /usr/local/bin/slurm-quota-serve
sudo rm -f /usr/local/bin/slurm-quota-web
sudo rm -f /etc/bash_completion.d/slurm-quota
sudo rm -f /etc/bash_completion.d/slurm-quota-prune
sudo rm -f /usr/local/share/man/man1/slurm-quota.1
sudo rm -f /usr/share/man/man1/slurm-quota.1
sudo rm -rf /usr/local/share/slurm-quota/web
```

3) Apply the [RPM packages (recommended)](#rpm-packages-recommended) procedure above (controller + compute/login nodes).

### Database Migrations

When using RPM packages, migration is automatically run during `slurm-quota-controller` installation/upgrade (only when the existing database file is present).

To force migration manually with RPM packages, run:

```bash
sudo /usr/libexec/slurm-quota/slurm-quota-migrate
```

For manual/source-based deployments, the database migration script must be executed before updating other components:

```bash
sudo slurm-quota-migrate
```

Example output:

```console
2025-12-04 10:11:42,926 - INFO - Adding array_size column to jobs_preallocations table
2025-12-04 10:11:42,938 - INFO - Migration completed: array_size column added
2025-12-04 10:11:42,939 - INFO - Database migration completed successfully
```

Then, the other components (`job_submit.lua`, `slurm-quota`, etc.) must be updated.

### Upgrade from version 2 to 3

Version 3 refactors the web dashboard into a proper Python package. The application logic is unchanged, but packaging and deployment paths differ from version 2.

**What changed**

- Web assets (`wsgi/`, `templates/`, `static/`) are installed under `slurm-quota/web/` (for example `/usr/share/slurm-quota/web/` on typical system installs) instead of `/usr/share/slurm-quota-web/`.
- `/usr/libexec/slurm-quota/slurm-quota-web` is no longer a WSGI script. It is now the standalone console entry point (Flask built-in HTTP server, for local testing only).
- Production Apache/mod_wsgi deployments must use the bundled WSGI script (for example `/usr/share/slurm-quota/web/wsgi/slurm-quota-web.wsgi`).

**Apache configuration**

After upgrading to version 3, update the virtual host configuration. Replace the v2 paths:

```apache
WSGIScriptAlias / /usr/libexec/slurm-quota/slurm-quota-web
Alias /static/ /usr/share/slurm-quota-web/static/
```

with the v3 paths:

```apache
WSGIScriptAlias / /usr/share/slurm-quota/web/wsgi/slurm-quota-web.wsgi
Alias /static/ /usr/share/slurm-quota/web/static/
```

Also update the matching `<Directory>` blocks. See the [RPM web dashboard](#rpm-packages-recommended) or [manual web dashboard](#web-dashboard-optional) Apache examples for a full v3 configuration.

Validate and reload Apache:

```bash
sudo apachectl configtest
sudo systemctl reload httpd
```

## Manpage

Manpages are maintained in AsciiDoc format:

- `man/slurm-quota.1.adoc` for `slurm-quota`
- `man/slurm-quota-charge.1.adoc` for `slurm-quota-charge`
- `man/slurm-quota-prune.1.adoc` for `slurm-quota-prune`
- `man/slurm-quota-serve.1.adoc` for `slurm-quota-serve`
- `man/slurm-quota-web.1.adoc` for `slurm-quota-web`

To generate roff manpages from these files, use:

```bash
asciidoctor -b manpage -o slurm-quota.1 man/slurm-quota.1.adoc
asciidoctor -b manpage -o slurm-quota-charge.1 man/slurm-quota-charge.1.adoc
asciidoctor -b manpage -o slurm-quota-prune.1 man/slurm-quota-prune.1.adoc
asciidoctor -b manpage -o slurm-quota-serve.1 man/slurm-quota-serve.1.adoc
asciidoctor -b manpage -o slurm-quota-web.1 man/slurm-quota-web.1.adoc
```

To preview generated files locally:

```bash
man -l ./slurm-quota.1
man -l ./slurm-quota-charge.1
man -l ./slurm-quota-prune.1
man -l ./slurm-quota-serve.1
man -l ./slurm-quota-web.1
```

Optional user-local installation:

```bash
install -Dm644 slurm-quota.1 ~/.local/share/man/man1/slurm-quota.1
install -Dm644 slurm-quota-charge.1 ~/.local/share/man/man1/slurm-quota-charge.1
install -Dm644 slurm-quota-prune.1 ~/.local/share/man/man1/slurm-quota-prune.1
install -Dm644 slurm-quota-serve.1 ~/.local/share/man/man1/slurm-quota-serve.1
install -Dm644 slurm-quota-web.1 ~/.local/share/man/man1/slurm-quota-web.1
```

## Development

### Tests

The repository includes unit tests under `tests/unit/` (one module per source module under `src/slurm_quota/`, with one `TestCase` class per function) and functional CLI tests under `tests/functional/` (one module per command). They are standard `unittest.TestCase` classes; the recommended runner is **pytest** (as in CI), with optional coverage reports configured in `pyproject.toml`.

From the repository root, use a virtual environment (recommended on distributions that restrict system-wide `pip`, e.g. PEP 668):

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install -U pip
python -m pip install ".[dev]"
```

Run the full suite (quiet mode, coverage for the loaded `slurm-quota` module, terminal + `coverage.xml`):

```bash
python -m pytest
```

## Acknowledgements

The development of this project was funded by [**ISDM-Meso**](https://isdm.umontpellier.fr/), part of the [University of Montpellier](https://www.umontpellier.fr/en/).

<p align="center">
  <a href="https://isdm.umontpellier.fr/">
    <img src="https://isdm.umontpellier.fr/wp-content/uploads/2025/02/Logo-ISDM-couleur.png" alt="ISDM (Institut des Sciences des Données de Montpellier) logo" height="60" />
  </a>
  <a href="https://www.umontpellier.fr/en/">
    <img src="https://www.umontpellier.fr/wp-content/uploads/2025/12/logo_um_2022_rouge_h73.png" alt="University of Montpellier logo" height="60" />
  </a>
</p>

ISDM stands for *Institut des Sciences des Données de Montpellier*. ISDM-Meso is the ISDM mesocentre (mesocenter), i.e. a shared mid-scale research computing facility providing HPC and data services to research teams, bridging local institutional resources and national/international supercomputing centers. This tool was developed in that operational context to support the administration of Slurm-based clusters.

## License

This project is licensed under the GNU General Public License v2.0 or later (GPL-2.0-or-later). See [LICENSE](LICENSE) for the full text.
