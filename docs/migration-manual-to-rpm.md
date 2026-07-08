# Migrate from manual installation to RPM packages

> [!NOTE]
> Use this procedure to switch an existing manual deployment to RPM-managed files. For RPM installation steps, see
> [Installation](../README.md#installation) in the main README.

1. Back up the database on the controller:

```bash
sudo sqlite3 /var/lib/state/slurm-quota/slurm-quota.db ".backup /var/lib/state/slurm-quota/slurm-quota-pre-rpm-$(date +%Y-%m-%d).db"
```

2. Remove legacy manually installed files that conflict with RPM-managed paths:

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
sudo rm -f /etc/bash_completion.d/slurm-quota-token
sudo rm -f /usr/local/share/man/man1/slurm-quota.1
sudo rm -f /usr/share/man/man1/slurm-quota.1
sudo rm -rf /usr/local/share/slurm-quota/web
```

3. Apply the [Installation](../README.md#installation) procedure in the main README (controller + compute/login nodes).
