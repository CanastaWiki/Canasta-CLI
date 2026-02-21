# Kubernetes (local)

Canasta CLI can deploy to a local Kubernetes cluster using [kind](https://kind.sigs.k8s.io/) (Kubernetes in Docker). This provides a Kubernetes-based alternative to Docker Compose for local development and testing.

## Contents

- [Prerequisites](#prerequisites)
- [Creating an installation](#creating-an-installation)
- [Managing the installation](#managing-the-installation)
- [How it works](#how-it-works)
- [Non-standard ports](#non-standard-ports)
- [Building from source](#building-from-source)
- [Current limitations](#current-limitations)

---

## Prerequisites

In addition to Docker (required for kind), you need:

| Tool | Purpose | Install |
|------|---------|---------|
| [kubectl](https://kubernetes.io/docs/tasks/tools/) | Kubernetes CLI | `brew install kubectl` or [see docs](https://kubernetes.io/docs/tasks/tools/) |
| [kind](https://kind.sigs.k8s.io/) | Local K8s clusters | `brew install kind` or [see docs](https://kind.sigs.k8s.io/docs/user/quick-start/#installation) |

On Linux, install kubectl and kind using your package manager or by downloading the binaries directly from the links above.

The CLI checks for these tools at creation time and will report a clear error if either is missing.

---

## Creating an installation

Use `-o k8s` with the `--local` flag:

```bash
canasta create -o k8s --local -i my-wiki -w main -a admin -n localhost
```

This automatically:
1. Creates a kind cluster named `canasta-my-wiki` with port mappings
2. Writes Kubernetes manifests and a `kustomization.yaml`
3. Applies the manifests with `kubectl apply`
4. Runs the MediaWiki installer

Once complete, your wiki is accessible at `https://localhost/main/`.

---

## Managing the installation

The same CLI commands work for both Compose and Kubernetes installations:

```bash
canasta stop -i my-wiki      # Scale deployments to 0 (cluster stays running)
canasta start -i my-wiki     # Scale deployments back up
canasta restart -i my-wiki   # Stop then start
canasta delete -i my-wiki    # Delete the kind cluster and all data
canasta upgrade -i my-wiki   # Update stack files and restart
```

**Stop** scales all Kubernetes deployments to zero replicas. The kind cluster and persistent volumes remain intact, so your data is preserved.

**Delete** removes the entire kind cluster, all Kubernetes resources, and the installation directory.

If the kind cluster is manually deleted (e.g., via `kind delete cluster`), `canasta start` will automatically recreate it. Note that persistent volume data will be lost in this case since it was stored inside the cluster.

---

## How it works

The CLI creates a kind cluster with `extraPortMappings` that map host ports directly to Kubernetes NodePort services:

```
Browser -> localhost:443 -> kind node:443 -> NodePort 30443 -> caddy pod -> MediaWiki
```

Each installation gets its own kind cluster (`canasta-<id>`) and Kubernetes namespace. The cluster configuration, including port mappings, is stored in the CLI's configuration file so it survives restarts.

### Directory structure

The installation directory has the same structure as a Compose installation, with Kubernetes manifests added:

```
my-wiki/
  .env                      # Environment variables (ports, passwords, image)
  kustomization.yaml         # Auto-generated — ties everything together
  kubernetes/                # Kubernetes manifests (namespace, deployments, services)
  config/
    wikis.yaml               # Wiki definitions
    Caddyfile, Caddyfile.site, Caddyfile.global
    settings/
      global/                # Settings loaded for all wikis
      wikis/<id>/            # Per-wiki settings
```

The `kustomization.yaml` is regenerated automatically by the CLI and should not be edited manually.

---

## Non-standard ports

By default, Canasta uses ports 80 (HTTP) and 443 (HTTPS). To use different ports, pass an env file:

```env
HTTP_PORT=8080
HTTPS_PORT=8443
```

```bash
canasta create -o k8s --local -i my-wiki -w main -a admin -n localhost:8443 -e custom.env
```

Each installation must use unique ports. If two installations attempt to bind the same host port, kind will fail with a Docker port-binding error. See [Running on non-standard ports](general-concepts.md#running-on-non-standard-ports) for more details.

---

## Building from source

To test a locally built Canasta image with a local K8s cluster, use `--build-from`:

```bash
canasta create -o k8s --local --build-from /path/to/workspace -i my-wiki -w main -a admin -n localhost
```

When `--local` is combined with `--build-from`, the CLI loads the built image directly into the kind cluster using `kind load docker-image`. No container registry is needed.

Without `--local`, the CLI pushes the image to a registry (default `localhost:5000`, configurable with `--registry`) so the remote cluster can pull it.

---

## Current limitations

Kubernetes support is focused on local development with kind. The following features are not yet available for Kubernetes installations:

| Feature | Status | Notes |
|---------|--------|-------|
| Development mode (Xdebug) | Not supported | Use Docker Compose for Xdebug debugging |
| Backup create | Not supported | Use `kubectl exec` to run mysqldump manually |
| Backup restore | Not supported | |
| Backup scheduling | Not supported | Use a Kubernetes CronJob instead |
| Remote clusters | Not tested | Manifests use `hostPath` volumes, which require a single-node cluster |
| Horizontal scaling | Limited | HPA is defined but not tested with the current volume setup |

For production deployments, Docker Compose remains the recommended orchestrator.
