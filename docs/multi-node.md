# Multi-node Kubernetes deployments

This guide walks through running Canasta on a multi-node Kubernetes
cluster. It covers provisioning the cluster (with AWS EC2 + k3s as a
worked example), shared storage, creating the instance, browsing to
the wiki, connecting to Argo CD for gitops, and scaling.

## When you need this

Multi-node multi-replica is appropriate if your wiki gets enough
traffic that a single web pod is a bottleneck, you need the wiki to
keep serving during node maintenance, or you're on a managed K8s
service (EKS, GKE, AKS) where multi-node is routine.

It is **not** a substitute for full HA. The default deployment keeps
several single-points-of-failure that multi-replica web alone does
not fix:

| Capability | Multi-node multi-replica web |
|---|---|
| Page-view load balancing | Yes |
| Survives a single web pod crash | Yes |
| Survives a node failure | Only if pods are spread across nodes |
| Database HA | No — still one DB pod on node-local storage |
| Caddy HA | No — still single-replica |

For true HA, add an external database (RDS Multi-AZ, Aurora, Cloud
SQL, Galera) and either put an external load balancer in front with
`--skip-tls`, or accept Caddy as a single-replica TLS terminator.

## Prerequisites

- A multi-node Kubernetes cluster you can reach with `kubectl`.
- ReadWriteMany-capable storage (NFS, EFS, CephFS, Longhorn-with-RWM,
  or similar). Without RWM, you can have multiple replicas but they
  cannot spread across nodes.
- `helm` 3.10+ on the controller (the host that will run `canasta`).
- A domain name with DNS control.
- Canasta CLI installed on the controller. See
  [Help:Installation](https://canasta.wiki/wiki/Help:Installation).

## Step 1 — Provision the cluster

Canasta-Ansible does not currently provision multi-node clusters for
you. Bring up the cluster yourself, then point Canasta at it.

### Self-managed k3s on AWS EC2 (worked example)

The same shape works on any cloud or bare metal — adapt the
security-group / firewall sections to your provider.

**Launch two EC2 instances** (one control plane, one worker; add more
later if needed):

- **AMI:** Ubuntu 22.04 or 24.04 (amd64)
- **Type:** at least 4 GiB RAM (`c7i-flex.large`, `t3.medium`). For
  Elasticsearch or heavy traffic use 8 GiB+.
- **Storage:** 20 GB gp3 EBS root volume (the default 8 GB Ubuntu
  AMI is too small for the Canasta image + k3s images + PVC space).
- **Key pair:** your SSH key.
- **Security group:**
  1. SSH (22/TCP) from your controller's IP
  2. HTTP (80/TCP) from anywhere — required for Let's Encrypt HTTP-01
  3. HTTPS (443/TCP) from anywhere
  4. K8s API (6443/TCP) from your controller's IP
  5. **All traffic (TCP + UDP)** between instances in the same
     security group. k3s uses VXLAN on UDP 8472 for cross-node pod
     networking; TCP-only will silently break cross-node DNS and
     ClusterIP services.

Record: `NODE1_IP` (control plane public IP), `NODE2_IP` (worker
public IP), `NODE1_PRIVATE` (control plane private IP).

**Install k3s on node 1 (control plane):**

```bash
ssh ubuntu@<NODE1_IP>
curl -sfL https://get.k3s.io | sudo sh -s - --tls-san <NODE1_IP>
sudo chmod 644 /etc/rancher/k3s/k3s.yaml   # so the controller can read it
sudo cat /var/lib/rancher/k3s/server/node-token   # save as <TOKEN>
exit
```

`--tls-san <NODE1_IP>` adds the public IP to the K8s API server's TLS
certificate so `kubectl` from the controller works. `chmod 644` makes
the kubeconfig readable by the SSH user so we can copy it back to the
controller in a later step.

`canasta install k8s` is *not* used here. It does install k3s, helm,
kubectl, and a 0644 kubeconfig — but it does not pass `--tls-san`,
so the API server's TLS cert won't include the public IP, and a
remote controller won't be able to talk to it. `canasta install k8s`
is fine for single-node deployments where the controller is itself
the cluster node.

**Join node 2 as a worker:**

There is no Canasta command for joining workers — run the upstream
installer with the join env vars on each worker:

```bash
ssh ubuntu@<NODE2_IP>
export K3S_URL=https://<NODE1_PRIVATE>:6443
export K3S_TOKEN=<TOKEN>
curl -sfL https://get.k3s.io | sudo -E sh -
exit
```

Repeat this block on any additional worker nodes later.

**Configure `kubectl` on the controller:**

```bash
mkdir -p ~/.kube
ssh ubuntu@<NODE1_IP> "sudo k3s kubectl config view --raw" > ~/.kube/config
# Replace 127.0.0.1 with the public IP.
# macOS:
sed -i '' "s/127.0.0.1/<NODE1_IP>/" ~/.kube/config
# Linux / WSL:
sed -i "s/127.0.0.1/<NODE1_IP>/" ~/.kube/config
chmod 600 ~/.kube/config
kubectl get nodes   # expect 2 nodes, both Ready
```

### Managed Kubernetes (EKS, GKE, AKS)

Skip k3s entirely. Provision the cluster as your cloud provider
documents, then configure kubeconfig on the controller:

- EKS: `aws eks update-kubeconfig --name <cluster> --region <region>`
- GKE: `gcloud container clusters get-credentials <cluster>`
- AKS: `az aks get-credentials --name <cluster>`

Verify `kubectl get nodes` before continuing. For real HA, launch
worker nodes in at least two availability zones.

### Verify with `canasta doctor`

```bash
canasta doctor
```

Flags missing dependencies (helm, kubectl) before you hit them later.

## Step 2 — Register hosts (optional)

Canasta can target remote hosts via SSH using short names saved in
`$CANASTA_CONFIG_DIR/hosts.yml`. If you plan to manage several
clusters from one controller, register each:

```bash
canasta host add --name node1 --ssh ubuntu@<NODE1_IP>
```

Then every subsequent command accepts `--host node1` to target that
cluster. If you're only managing one cluster and running `canasta`
directly on the controller with a local kubeconfig, you can skip this
step.

## Step 3 — Provision shared storage

### NFS (works anywhere)

The simplest NFS target is the control plane node itself.
`canasta storage setup nfs --install-server` installs the server
package, creates the share directory, exports it, and installs the
NFS CSI driver + StorageClass on the cluster in one step:

```bash
canasta storage setup nfs \
  --install-server \
  --share /srv/nfs/canasta \
  --storage-class-name nfs
```

If the NFS server is a separate host, pass `--server <IP>` instead of
`--install-server`.

```bash
kubectl get storageclass   # expect 'nfs' listed
```

In production you'd usually use a dedicated NFS host or managed NFS
(EFS, Filestore, Azure Files) — colocating NFS with the control plane
couples storage availability to that node.

### AWS EFS

For EKS, EFS is the natural fit:

1. Create an EFS filesystem in the EKS cluster's VPC.
2. Add mount targets in each AZ with worker nodes.
3. Allow NFS (TCP 2049) from worker SGs to the EFS mount target SG.
4. Set up IRSA so the EFS CSI driver can manage access points.

```bash
canasta storage setup efs --filesystem-id fs-0123456789abcdef0
```

### Bring your own RWM storage

CephFS, Longhorn-with-RWM, NetApp Trident, vendor CSI — confirm with
`kubectl get storageclass` that an RWM-capable class exists, and use
its name below.

## Step 4 — DNS

Point your domain at any node IP (k3s's Traefik ingress listens on
80/443 on every node). On managed K8s, point at the load balancer.

```
wiki.example.com  A  <NODE1_IP>
```

Verify: `dig +short wiki.example.com`.

For subdomain-routed wiki farms, add a wildcard:
`*.wiki.example.com → <NODE1_IP>`.

## Step 5 — Create the instance

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
multi-replica web. `ReadWriteMany` declares the four content PVCs as
RWM so they can mount from multiple nodes at once; without it they
default to RWO and the chart's declared contract will be wrong, even
if the NFS CSI driver's leniency makes it look like it works.

When a real domain name is used, cert-manager and Let's Encrypt are
configured automatically — no manual TLS setup.

Verify:

```bash
kubectl get pods -n canasta-mywiki   # all Running
kubectl get pvc -n canasta-mywiki    # content PVCs RWX on nfs
kubectl get certificate -n canasta-mywiki   # Ready=True
```

## Step 6 — Browse to the wiki

Open `https://wiki.example.com/wiki/Main_Page` in a browser. You
should see the MediaWiki main page with a valid Let's Encrypt
certificate (green padlock, no warnings).

If the certificate isn't ready yet, give cert-manager 30–60 seconds
to complete the ACME challenge. Check status with
`kubectl describe certificate -n canasta-mywiki`.

## Step 7 — Argo CD dashboard

`canasta create --orchestrator kubernetes` installs Argo CD by
default. To open the UI:

```bash
# Get the admin password:
kubectl get secret argocd-initial-admin-secret -n argocd \
  -o jsonpath='{.data.password}' | base64 -d ; echo

# Port-forward (run in a separate terminal):
kubectl port-forward svc/argocd-server -n argocd 8443:443
```

Browse `https://localhost:8443`, accept the self-signed certificate,
log in as `admin`. Applications managed via `canasta gitops` show up
here as synced Argo CD Applications.

## Step 8 — Connect to gitops

Put the instance's configuration under git so Argo CD can sync
changes from the repo:

```bash
canasta gitops init \
  --id mywiki \
  -n $(hostname) \
  --repo git@github.com:<org>/<repo>.git \
  --key ~/mywiki-deploy-key
```

The command pauses and prints an SSH public key — add it as a deploy
key with write access at `https://github.com/<org>/<repo>/settings/keys`,
then press Enter. Argo CD picks up the repo and starts syncing.

```bash
canasta gitops status --id mywiki
kubectl get application -n argocd   # expect Synced / Healthy
```

## Step 9 — Scale the web tier

The generated `values.yaml` does not include a `web:` section by
default — it inherits `web.replicaCount: 1` from the chart. To scale:

```bash
canasta list                        # note the instance path
# Add to <instance_path>/values.yaml before 'domains:':
#   web:
#     replicaCount: 3
canasta restart --id mywiki
kubectl get pods -n canasta-mywiki -o wide | grep web
```

There is no `canasta scale` command yet — editing `values.yaml` and
running `canasta restart` is the canonical way. The restart flow
reads `web.replicaCount` and applies it via `helm upgrade`. See
"Known gaps" below.

### Pod spread caveat

The K8s scheduler tries to distribute pods by resource availability
but does **not guarantee** spread. With `replicaCount: 3` on two
nodes you may get 2-1 or 3-0 depending on the scheduler. If you need
guaranteed spread, cordon the crowded node to force reschedule:

```bash
kubectl cordon <node-name>
canasta restart --id mywiki
kubectl uncordon <node-name>
```

## Adding more workers

```bash
# On the new node:
ssh ubuntu@<NEW_NODE_IP>
export K3S_URL=https://<NODE1_PRIVATE>:6443
export K3S_TOKEN=<TOKEN>   # same token used earlier; retrieve from node 1 if lost
curl -sfL https://get.k3s.io | sudo -E sh -
exit

# From the controller:
kubectl get nodes   # new node should be Ready within ~30s
```

Shared storage (NFS) and ingress (Traefik) already cover any number
of nodes — no reconfiguration needed. New web replicas (after
`canasta restart`) can schedule onto the new node.

## Cleanup

```bash
canasta delete --id mywiki --yes
# Terminate the EC2 instances in the AWS console to stop billing.
```

## Windows / WSL notes

Everything in this guide works unmodified on WSL2 Ubuntu. Watch out
for:

- **SSH key permissions.** If the AWS key lives on `/mnt/c/...`, WSL
  sees it as mode `0777` and `ssh` refuses to use it. Copy the key
  into `~/.ssh/` inside WSL and `chmod 600` it.
- **`dig` isn't preinstalled** — `sudo apt install dnsutils`.
- **Installing the Canasta CLI** — follow
  [Help:Installation](https://canasta.wiki/wiki/Help:Installation).
  Native mode (Python + Ansible) and Docker mode both work under
  WSL2; some host-mount quirks seen with `canasta-docker` on macOS
  may surface on Windows too.
- **Argo CD port-forward.** WSL2 auto-forwards localhost to the
  Windows host, so `https://localhost:8443` works from the Windows
  browser without extra setup.
- **`sed` syntax.** Use the Linux form (no `''` after `-i`) — the
  macOS form errors on WSL.

## Caveats and known limitations

### Database

The default deployment runs **one** MariaDB pod on node-local
(`local-path`) storage. A node failure on the DB's node takes the
wiki down regardless of web replica count. For production HA, point
Canasta at an external managed database (RDS, Aurora, Cloud SQL, or a
Galera cluster). See issue #57 (external DB support).

### Caddy

Caddy is single-replica. The `caddy-data` PVC holds Let's Encrypt
certificates and is RWO; multiple replicas would also independently
provision certs and hit ACME rate limits. For an HA TLS layer, put an
external load balancer in front of Canasta and disable Canasta's TLS
with `--skip-tls`.

### Elasticsearch / OpenSearch

Both run single-node by default (`discovery.type=single-node`
hardcoded). Clustered Elasticsearch needs out-of-chart configuration.

### Pod scheduling

The chart doesn't declare pod anti-affinity or topology spread
constraints — multi-replica spread is best-effort. Use the cordon
workaround in Step 9 to force placement.

## Known gaps

- **`canasta install k8s` doesn't pass `--tls-san`.** Fine for
  single-node deployments where the controller is the cluster node;
  for multi-node with a remote controller, the API server's cert
  won't include the public IP and `kubectl get nodes` from the
  controller fails TLS verification. Manual `curl … --tls-san …`
  is required on the control-plane node for now.
- **No `canasta` command for joining worker nodes.** Workers must
  be brought up with the upstream installer plus `K3S_URL` /
  `K3S_TOKEN` env vars on each new worker.
- **No `canasta scale` command.** Changing `web.replicaCount` means
  editing `values.yaml` and running `canasta restart`.

## Troubleshooting

**Pods on the worker node can't resolve DNS or reach ClusterIP
services.** VXLAN/UDP blocked between nodes. Verify the security
group allows all UDP (not just TCP) between cluster nodes. Smoking
gun: web pod's `wait-for-db` init container hangs on DNS lookup.

**PVCs stuck `Pending`.** `kubectl describe pvc <name>` shows the
cause. Usually: StorageClass missing, StorageClass doesn't support
RWM, or CSI driver pod not running (check IAM on EKS).

**Web replicas Pending after scale-up.** RWO PVC already attached to
a different node. Recreate the instance with
`--access-mode ReadWriteMany`.

**Pods all on one node.** Scheduler doesn't guarantee spread. Use
the cordon workaround (Step 9).

**Certificate stuck `Ready=False`.** Usually DNS hasn't propagated,
or port 80 isn't open from the internet (Let's Encrypt HTTP-01
challenge requires reachable port 80).
