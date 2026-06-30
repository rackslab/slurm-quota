# Upgrade from version 2 to 3

Version 3 refactors the web dashboard into a proper Python package. The application logic is unchanged, but packaging and deployment paths differ from version 2.

## What changed

- Web assets (`wsgi/`, `templates/`, `static/`) are installed under `slurm-quota/web/` (for example `/usr/share/slurm-quota/web/` on typical system installs) instead of `/usr/share/slurm-quota-web/`.
- `/usr/libexec/slurm-quota/slurm-quota-web` is no longer a WSGI script. It is now the standalone console entry point (Flask built-in HTTP server, for local testing only).
- Production Apache/mod_wsgi deployments must use the bundled WSGI script (for example `/usr/share/slurm-quota/web/wsgi/slurm-quota-web.wsgi`).

## Apache configuration

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

> [!NOTE]
> Also update the matching `<Directory>` blocks. See the [Web dashboard](../README.md#web-dashboard) section in the main README or [manual web dashboard](installation-manual.md#web-dashboard) Apache examples for a full v3 configuration.

Validate and reload Apache:

```bash
sudo apachectl configtest
sudo systemctl reload httpd
```
