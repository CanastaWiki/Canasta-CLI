# Orchestrators

Canasta CLI supports two orchestrators for running your wiki stack:

- **Docker Compose** (default) — the recommended option for most users
- **Kubernetes** — for users who need Kubernetes, with CLI-managed kind clusters or existing clusters

Both orchestrators share the same CLI commands (`canasta start`, `canasta stop`, `canasta upgrade`, etc.) and the same installation directory layout (`.env`, `config/`, `extensions/`, `skins/`, `images/`).

## Contents

- [Docker Compose](#docker-compose)
  - [docker-compose.override.yml](#docker-composeoverrideyml)
- [Kubernetes](#kubernetes)
  - [Prerequisites](#prerequisites)
  - [Creating an installation (managed cluster)](#creating-an-installation-managed-cluster)
  - [Managing the installation](#managing-the-installation)
  - [How it works](#how-it-works)
  - [Non-standard ports](#non-standard-ports)
  - [Building from source](#building-from-source)
  - [Using an existing cluster](#using-an-existing-cluster)
  - [Current limitations](#current-limitations)

---

## Docker Compose

Docker Compose is the default orchestrator. No `-o` flag is needed:

```bash
canasta create -i myinstance -w main -n localhost
```

This creates a standard Docker Compose stack with containers for the web server, database, Caddy reverse proxy, and other services. All data is stored on the host in the installation directory.

Docker Compose supports all Canasta features including [development mode](devmode.md), [backup and restore](backup.md), and [observability](observability.md).

### docker-compose.override.yml

Docker Compose automatically merges `docker-compose.override.yml` with the main `docker-compose.yml` when present. Use this file to customize services without modifying the managed stack files.

You can provide an override file at creation time with the `-r` flag:

```bash
canasta create -i myinstance -w main -n localhost -r my-overrides.yml
```

Or create/edit the file directly in the installation directory at any time.

Common use cases:

**Custom service images** — build a service with additional plugins or configuration:

```yaml
services:
  elasticsearch:
    build:
      context: ./build
      dockerfile: Dockerfile.elasticsearch
    image: canasta-elasticsearch-icu:7.10.2
```

See [Custom Elasticsearch plugins](extensions-and-skins.md#custom-elasticsearch-plugins) for a full example.

**Extra volumes** — mount additional directories into a container:

```yaml
services:
  web:
    volumes:
      - ./my-data:/var/www/mediawiki/w/my-data
```

**Environment variables** — add or override environment variables for a service:

```yaml
services:
  web:
    environment:
      - MY_CUSTOM_VAR=value
```

After editing the override file, restart the installation for changes to take effect:

```bash
canasta restart -i myinstance
```

The override file is included in [backups](backup.md).

---

## Kubernetes

Canasta CLI can deploy to Kubernetes in two ways:

- **Managed cluster** (`--create-cluster`): The CLI creates and manages a local [kind](https://kind.sigs.k8s.io/) cluster for you. This is the recommended approach for local development and testing.
- **Existing cluster**: You provide a pre-configured Kubernetes cluster and the CLI deploys to it. This is experimental and has [significant limitations](#using-an-existing-cluster).

### Prerequisites

#### Managed cluster (recommended)

In addition to Docker (required for kind), you need:

| Tool | Purpose | Install (macOS) | Install (Linux) |
|------|---------|-----------------|-----------------|
| [kubectl](https://kubernetes.io/docs/tasks/tools/) | Kubernetes CLI | `brew install kubectl` | [See docs](https://kubernetes.io/docs/tasks/tools/) |
| [kind](https://kind.sigs.k8s.io/) | Local K8s clusters | `brew install kind` | [See docs](https://kind.sigs.k8s.io/docs/user/quick-start/#installation) |

The CLI checks for these tools at creation time and will report a clear error if either is missing.

#### Existing cluster

You need `kubectl` installed and configured to connect to your cluster (`kubectl cluster-info` must succeed).

---

### Creating an installation (managed cluster)

Use `-o k8s` with the `--create-cluster` flag:

```bash
canasta create -o k8s --create-cluster -i my-wiki -w main -n localhost
```

This automatically:
1. Creates a kind cluster named `canasta-my-wiki` with port mappings
2. Writes Kubernetes manifests and a `kustomization.yaml`
3. Applies the manifests with `kubectl apply`
4. Runs the MediaWiki installer

Once complete, your wiki is accessible at `https://localhost/main/`.

---

### Managing the installation

The same CLI commands work for both Compose and Kubernetes installations:

```bash
canasta stop -i my-wiki      # Scale deployments to 0 (cluster stays running)
canasta start -i my-wiki     # Scale deployments back up
canasta restart -i my-wiki   # Stop then start
canasta delete -i my-wiki    # Delete the kind cluster and all data
canasta upgrade              # Update CLI and all installations
```

**Stop** scales all Kubernetes deployments to zero replicas. The kind cluster and persistent volumes remain intact, so your data is preserved.

**Delete** removes the entire kind cluster, all Kubernetes resources, and the installation directory.

If the kind cluster is manually deleted (e.g., via `kind delete cluster`), `canasta start` will automatically recreate it. Note that persistent volume data will be lost in this case since it was stored inside the cluster. If the installation was created with `--build-from`, the locally built image will also need to be rebuilt and reloaded.

---

### How it works

With `--create-cluster`, the CLI creates a kind cluster with `extraPortMappings` that map host ports directly to Kubernetes NodePort services:

```
Browser -> localhost:443 -> kind node:443 -> NodePort 30443 -> caddy pod -> MediaWiki
```

Each installation gets its own kind cluster (`canasta-<id>`) and Kubernetes namespace. The cluster configuration, including port mappings, is stored in the CLI's configuration file so it survives restarts.

#### Directory structure

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

### Non-standard ports

By default, Canasta uses ports 80 (HTTP) and 443 (HTTPS). To use different ports, pass an env file:

```env
HTTP_PORT=8080
HTTPS_PORT=8443
```

```bash
canasta create -o k8s --create-cluster -i my-wiki -w main -n localhost:8443 -e custom.env
```

Each installation must use unique ports. If two installations attempt to bind the same host port, kind will fail with a Docker port-binding error. See [Running on non-standard ports](general-concepts.md#running-on-non-standard-ports) for more details.

---

### Building from source

To test a locally built Canasta image with a managed K8s cluster, use `--build-from`:

```bash
canasta create -o k8s --create-cluster --build-from /path/to/workspace -i my-wiki -w main -n localhost
```

When `--create-cluster` is combined with `--build-from`, the CLI loads the built image directly into the kind cluster using `kind load docker-image`. No container registry is needed.

Without `--create-cluster`, the CLI pushes the image to a registry (default `localhost:5000`, configurable with `--registry`) so the cluster can pull it.

---

### Using an existing cluster

To deploy to a pre-existing Kubernetes cluster without `--create-cluster`:

```bash
canasta create -o k8s -i my-wiki -w main -n my-wiki.example.com
```

This mode is **experimental** and has significant limitations:

- The manifests use `hostPath` volumes pointing to `/canasta/config-data`, which only work on single-node clusters. Multi-node clusters will not work correctly.
- Services use LoadBalancer type by default. You must configure your cluster's load balancer or ingress separately.
- The CLI does not manage the cluster lifecycle — you are responsible for creating, maintaining, and deleting the cluster.
- For locally built images (`--build-from`), you need a container registry accessible from the cluster (`--registry` flag, default `localhost:5000`).

For most users, `--create-cluster` is the recommended approach.

---

### Current limitations

The following features are not yet available for Kubernetes installations:

| Feature | Status | Notes |
|---------|--------|-------|
| Development mode (Xdebug) | Not supported | Use Docker Compose for Xdebug debugging |
| Remote/multi-node clusters | Not supported | Manifests use `hostPath` volumes |
| Horizontal scaling | Limited | HPA is defined but not tested with the current volume setup |

For production deployments, Docker Compose remains the recommended orchestrator.
