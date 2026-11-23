# Development cluster

This directory contains the settings to deploy `vortex`, an emulated HPC cluster based on
[FireHPC](https://github.com/rackslab/FireHPC), to run functional tests of slurm-quota on a real Slurm cluster.

- `firehpc/db/`: [RacksDB](https://github.com/rackslab/RacksDB) database describing the cluster nodes: compute nodes
  `cn[1-4]`, GPU nodes `ia[1-2]` (Nvidia H100 and H200), `login` and `admin`.
- `firehpc/conf/vortex/`: FireHPC variables to customize the cluster: Rackslab `pkgs` and `devs` packages repositories,
  Slurm 24.11 from HPCkit, partitions `standard` and `ia`, Slurm settings required by slurm-quota (`jobcomp/script` with
  the charge wrapper, `job_submit.lua`, GPU TRES accounting) and slurm-quota deployment.

## Deployment

From the root of the repository, deploy the cluster with Rocky Linux 9:

```sh
firehpc deploy --db devs/firehpc/db --cluster vortex --os rocky9 --custom devs/firehpc/conf/vortex
```

Check the cluster status:

```sh
firehpc status --cluster vortex
```

## Usage

Connect to the admin node as root:

```sh
firehpc ssh admin.vortex
```

Connect to the login node with a generated user account to submit jobs:

```sh
firehpc ssh <user>@login.vortex
```

## Configuration update

After modifying settings in `firehpc/conf/vortex/`, deploy the configuration on the existing cluster:

```sh
firehpc conf --db devs/firehpc/db --cluster vortex --custom devs/firehpc/conf/vortex
```

## Cleanup

Destroy the cluster:

```sh
firehpc clean --cluster vortex
```
