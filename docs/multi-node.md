# Multi-node Kubernetes deployments

This guide walks through running Canasta on a multi-node Kubernetes
cluster, with multiple web pods spread across nodes for load balancing
and improved availability.

## When you need this

You probably want a multi-node multi-replica deployment if any of these
are true:

- Your wiki gets enough traffic that a single web pod is a bottleneck.
- You need the wiki to keep serving page views during a node restart or
  routine maintenance.
- You're running on a managed Kubernetes service (EKS, GKE, AKS) where
  multi-node clusters are the default and node failures are routine.

You probably do **not** need this if:

- You're running a small wiki on a single VM or single-node cluster.
- Your traffic is low enough that a single web pod handles it without
  load.
- The database is your single point of failure (it usually is — see
  the "Caveats and known limitations" section below before assuming
  multi-replica web alone gives you HA).

## What "multi-node multi-replica" actually buys you

| Capability | Single-node default | Multi-node multi-replica web |
|---|---|---|
| Page-view load balancing across pods | No (one pod) | Yes |
| Survives a single web pod crash | Yes (K8s reschedules) | Yes |
| Survives a node failure | No (everything goes down) | **Only if pods are spread across nodes** — see caveats below |
| Database HA | No | No (still a single DB pod by default; see "Caveats") |
| Caddy HA | No | No (Caddy is still single-replica; see "Caveats") |

So this guide gets you part of the way to a fully highly available
deployment. The rest depends on what you do about the database, which
is the bigger HA story.

## Prerequisites

- A multi-node Kubernetes cluster you can reach with `kubectl`.
- ReadWriteMany-capable storage (NFS, EFS, CephFS, Longhorn-with-RWM,
  or similar). Without RWM, you can have multiple replicas but they
  cannot be spread across nodes.
- `helm` 3.10 or newer on the host where you'll run `canasta`.
- A domain name pointed at your cluster's ingress (for production HTTPS).
- The Canasta-Ansible CLI installed and your controller's hosts.yml
  configured to reach the host that has `kubectl` access to the cluster.

## Step 1 — Provision the cluster

Canasta-Ansible does not currently provision multi-node clusters for
you. You need to bring up the cluster yourself, then point Canasta at
it.

### Self-managed k3s on cloud VMs (worked example: AWS EC2)

This example uses two AWS EC2 instances. The same shape works on any
cloud or on bare metal — just adapt the security-group / firewall
sections to your provider.

**EC2-specific bits**: launching the instances, security group rules,
the use of private vs public IPs, and the `--tls-san` flag for the
public IP. On other clouds (GCE, Azure, Hetzner, bare metal) the
analogous concepts exist but the commands differ.

#### 1a. Launch two instances

```bash
# Launch 2 EC2 instances:
#   - AMI: Ubuntu 22.04 or 24.04 (amd64)
#   - Type: needs at least 4 GiB of total RAM. Working choices for
#     a small wiki without Elasticsearch include c7i-flex.large
#     (4 GiB), t3.medium (4 GiB), t3.large (8 GiB), and
#     c7i-flex.xlarge (8 GiB). For wikis with Elasticsearch enabled
#     or with significant traffic, use 8 GiB or larger.
#   - Storage: 20 GB gp3 EBS root volume or larger. The default
#     8 GB Ubuntu AMI is too small once the Canasta image, k3s
#     images, and PVC working space are accounted for.
#   - Key pair: your SSH key
#   - Security group rules:
#     1. SSH (22/TCP) from your controller's IP
#     2. HTTP (80/TCP) from anywhere (required for Let's Encrypt
#        HTTP-01 challenge)
#     3. HTTPS (443/TCP) from anywhere
#     4. K8s API (6443/TCP) from your controller's IP
#     5. *** All traffic (TCP + UDP) between instances in the same
#        security group ***
```

The fifth rule is the one that catches people. **k3s uses VXLAN on
UDP port 8472 for pod-to-pod networking across nodes.** If your
security group only allows TCP between nodes, cross-node pod
networking will silently fail — pods on the worker node will be
unable to reach ClusterIP services (including CoreDNS) hosted on
other nodes, and the symptoms look like generic DNS resolution
failures inside pods. Always allow all UDP between nodes, not just
all TCP.

On other clouds, look for the equivalent: GCP firewall rules need
"Allow all internal traffic" between cluster nodes; Azure NSGs
need both TCP and UDP between subnets; etc.

Record these values from the AWS console or CLI:

- `NODE1_IP` — public IP of node 1 (will be the control plane)
- `NODE2_IP` — public IP of node 2 (will be the worker)
- `NODE1_PRIVATE` — **private** IP of node 1 (used for in-cluster
  communication; the worker node joins via this address)

#### 1b. Install k3s server on node 1

```bash
ssh ubuntu@<NODE1_IP>
curl -sfL https://get.k3s.io | sudo sh -s - --tls-san <NODE1_IP>
sudo cat /var/lib/rancher/k3s/server/node-token
# Save this token: <TOKEN>
exit
```

**EC2-specific note**: `--tls-san <NODE1_IP>` adds the public IP to
the K8s API server's TLS certificate. Without it, kubectl from your
controller laptop fails TLS validation when connecting to the public
IP. On a cluster where the controller and k8s nodes share an internal
network and the controller can reach the k8s API server by its
internal IP, you don't need `--tls-san`.

#### 1c. Join node 2 as a k3s worker

```bash
ssh ubuntu@<NODE2_IP>
export K3S_URL=https://<NODE1_PRIVATE>:6443
export K3S_TOKEN=<TOKEN>
curl -sfL https://get.k3s.io | sudo -E sh -
exit
```

This is vanilla k3s — no Canasta involvement. The same pattern works
for adding more worker nodes later: each one runs the same `curl |
sh` with the same `K3S_URL` and `K3S_TOKEN`.

#### 1d. Configure kubectl on the controller

```bash
mkdir -p ~/.kube
ssh ubuntu@<NODE1_IP> "sudo k3s kubectl config view --raw" > ~/.kube/config

# Replace 127.0.0.1 with the public IP so the controller can reach
# the API server. macOS:
sed -i '' "s/127.0.0.1/<NODE1_IP>/" ~/.kube/config
# Linux:
# sed -i "s/127.0.0.1/<NODE1_IP>/" ~/.kube/config

chmod 600 ~/.kube/config
kubectl get nodes
# Expected: 2 nodes, both Ready
```

### Managed Kubernetes (EKS, GKE, AKS)

Skip k3s entirely. Provision your managed cluster the way your cloud
provider documents — `eksctl`, Terraform, the cloud console, etc.
Make sure to:

- Launch worker nodes in at least two availability zones for real HA.
- Configure your kubeconfig on the controller with whatever
  cloud-specific tooling exists. For EKS this is
  `aws eks update-kubeconfig --name <cluster> --region <region>`;
  for GKE it's `gcloud container clusters get-credentials`; for AKS
  it's `az aks get-credentials`.
- Verify with `kubectl get nodes` that the controller can reach the
  cluster.

Once kubectl works, the rest of this guide is the same — Canasta
treats any K8s cluster the same way regardless of where it came from.

## Step 2 — Provision shared storage

### Option A: NFS (works anywhere)

You need an NFS server reachable from every node in the cluster. The
simplest way is to install one on the same node that runs the k3s
control plane, exporting a directory via NFS.

```bash
# On node 1:
ssh ubuntu@<NODE1_IP>
sudo apt-get update -qq
sudo apt-get install -y nfs-kernel-server
sudo mkdir -p /srv/nfs/canasta
echo "/srv/nfs/canasta *(rw,sync,no_subtree_check,no_root_squash)" \
  | sudo tee -a /etc/exports
sudo exportfs -ra
exit
```

In production you'd usually use a dedicated NFS host or managed NFS
(EFS on AWS, Filestore on GCP, Azure Files on Azure) — running NFS on
the same node as the K8s control plane is fine for testing but
couples the storage's availability to that node's availability.

Then install the NFS CSI driver and create a StorageClass:

```bash
# From your controller:
canasta storage setup nfs \
  --server <NODE1_PRIVATE> \
  --share /srv/nfs/canasta \
  --storage-class-name nfs

kubectl get storageclass
# Expected: nfs StorageClass listed
```

`canasta storage setup nfs` runs against the host you target with
`--host` (defaulting to the local controller). It installs the
`csi-driver-nfs` Helm chart on whatever K8s cluster is in the
target's kubeconfig and creates a StorageClass pointing at the NFS
server you specified.

### Option B: AWS EFS

For EKS or any other AWS-hosted cluster, EFS is the natural fit. It's
managed, multi-AZ, and the EFS CSI driver is well-supported.

1. Create an EFS filesystem in the same VPC as your EKS cluster.
2. Add mount targets in each AZ where worker nodes run.
3. Configure security groups to allow NFS (TCP 2049) from worker
   security groups to the EFS mount target security group.
4. Set up IRSA (IAM Roles for Service Accounts) so the EFS CSI driver
   can manage access points. AWS documentation walks through this.

Then:

```bash
canasta storage setup efs --filesystem-id fs-0123456789abcdef0
```

This installs the AWS EFS CSI driver and creates an `efs`
StorageClass.

### Option C: bring-your-own RWM storage

If you already have RWM-capable storage on the cluster (CephFS,
Longhorn-with-RWM, NetApp Trident, vendor-specific CSI drivers, etc.),
you don't need `canasta storage setup` at all. Just verify with
`kubectl get storageclass` that an RWM-capable class exists, and use
its name when creating the instance below.

## Step 3 — DNS

Point your domain at the public IP of one of the cluster nodes. k3s
includes Traefik as the default ingress controller, listening on
ports 80 and 443 on every node, so the IP can be any node.

```bash
# Create an A record:
#   wiki.example.com  →  <NODE1_IP>

dig +short wiki.example.com
# Expected: <NODE1_IP>
```

For wiki farms with subdomain routing, add a wildcard:
`*.wiki.example.com → <NODE1_IP>`.

On managed Kubernetes services, you'll typically have a load balancer
in front of the ingress controller, and DNS points at the load
balancer rather than at a node IP directly.

## Step 4 — Create the instance

```bash
canasta create \
  --id mywiki \
  --wiki main \
  --domain-name wiki.example.com \
  --orchestrator kubernetes \
  --storage-class nfs \
  --access-mode ReadWriteMany
```

Both `--storage-class` and `--access-mode` are needed for multi-node
multi-replica web. The two together tell the chart "use the NFS
storage class for the four content PVCs, and declare them as
ReadWriteMany so they can be mounted from multiple nodes
simultaneously."

If you omit `--access-mode`, the PVCs default to `ReadWriteOnce`. They
will then physically work on most NFS implementations because the
NFS CSI driver is lenient about access mode enforcement, but the
chart's declared contract will be wrong, and the same setup will fail
on stricter RWM backends like EFS or CephFS.

After `canasta create` completes:

```bash
kubectl get pods -n canasta-mywiki
# Expected: web, caddy, varnish, db, jobrunner — all Running

kubectl get pvc -n canasta-mywiki
# Expected: images, extensions, skins, public-assets bound on the nfs
#           StorageClass with ACCESS MODES = RWX
#           (db-data uses local-path by default — see Caveats)
```

## Step 5 — Scale the web tier

The generated `values.yaml` does not include a `web:` section by
default — it inherits `web.replicaCount: 1` from the chart defaults.
To scale up, you need to **add** the section:

```bash
# Find the instance path:
canasta list

# Add a web: section to instance_path/values.yaml. For example,
# insert before the "domains:" line:
#
#   web:
#     replicaCount: 3
#
# Or via sed:
sed -i '' '/^domains:/i\
web:\
  replicaCount: 3\
' <instance_path>/values.yaml

# Then restart so the helm upgrade picks up the change:
canasta restart --id mywiki
```

There is no `canasta scale` command yet — the canonical way to change
replica counts is to edit `values.yaml` and run `canasta restart`. The
restart flow reads `web.replicaCount` from `values.yaml` and applies
it via `helm upgrade`.

```bash
kubectl get pods -n canasta-mywiki -o wide | grep web
# Expected: 3 web pods, ideally on multiple nodes
```

### Pod spread caveat

The default Kubernetes scheduler will try to distribute pods across
nodes based on resource availability, but **it does not guarantee
spread**. With `replicaCount: 3` and two nodes, you may get two pods
on one node and one on the other, or all three on the same node, or
2-1, depending on what the scheduler thinks is optimal at the time.

If you need stricter spread guarantees, you can manually force a
re-schedule by cordoning the node where pods are clustered:

```bash
kubectl cordon <node-name>      # mark node unschedulable
canasta restart --id mywiki     # forces rescheduling
kubectl uncordon <node-name>    # re-allow scheduling
```

If your workload depends on guaranteed pod spread for fault
tolerance, the cordon workaround above is the current solution.

## Caveats and known limitations

Multi-node multi-replica web is **not** the same as full HA. Several
single-points-of-failure remain by default:

### Database

The default deployment runs **one** MariaDB pod backed by a
node-local PVC (`local-path` storage class on k3s). If the node
hosting the DB pod fails, the DB pod cannot reschedule until that
node recovers, and the entire wiki goes down regardless of how many
web replicas you have.

For production high availability, the recommended approach is to use
an external database service (a managed MariaDB or MySQL such as RDS
Multi-AZ, Aurora, Cloud SQL, or a self-managed Galera cluster) and
point Canasta at it instead of running the bundled DB pod. The
external database handles its own replication, failover, and backups,
which removes the database tier as a single point of failure for the
wiki.

If you keep the bundled DB pod, "multi-replica web with single-pod
local-path DB" is useful for load distribution and partial fault
tolerance — any node failure that isn't the DB node leaves the wiki
running — but should not be confused with full high availability.

### Caddy

Caddy is single-replica in the default chart. The `caddy-data` PVC
stores TLS certificates and is `ReadWriteOnce`, and multiple Caddy
replicas would also independently provision Let's Encrypt
certificates and hit ACME rate limits. If you need a highly available
HTTPS termination layer, the supported approach is to put an external
load balancer or ingress controller in front of Canasta and disable
Canasta's own TLS handling with the `--skip-tls` flag at create time.

### Elasticsearch / OpenSearch

Both run as single-node by default with `discovery.type=single-node`
hardcoded. If you need a clustered Elasticsearch, you currently have
to configure it yourself outside the chart.

### Pod scheduling

As noted in Step 5, the chart does not declare pod anti-affinity or
topology spread constraints. Multi-replica spread is best-effort,
based on the Kubernetes scheduler's default scoring (resource
availability and balance). Use the cordon workaround above if you
need to force pods onto specific nodes.

## Troubleshooting

**Pods on the worker node cannot resolve DNS or reach ClusterIP
services.** This is the VXLAN/UDP problem on EC2 (and analogous
issues on other clouds). Verify the security group allows all UDP
between cluster nodes, not just TCP. The smoking-gun symptom is the
web pod's `wait-for-db` init container hanging on DNS lookup of `db`.

**PVCs stuck in `Pending`.** Run `kubectl describe pvc <name>` to see
the actual binding error. Common causes:

- StorageClass doesn't exist (check `kubectl get storageclass`).
- StorageClass exists but doesn't support the requested access mode
  (e.g. asking for `ReadWriteMany` against a class backed by EBS).
- Provisioner is failing (CSI driver pod is not running or doesn't
  have the right IAM permissions on EKS).

**Web replicas pending after scale-up.** Likely an RWO PVC is already
attached to a different node. Either set `--access-mode ReadWriteMany`
when creating the instance, or accept that all replicas must land on
the node holding the PVC.

**Pods all on one node despite multi-node cluster.** The Kubernetes
scheduler doesn't guarantee spread without anti-affinity rules. Use
the cordon workaround in Step 5 to force a re-schedule.
