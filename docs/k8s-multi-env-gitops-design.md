# K8s Multi-Environment GitOps Design

## Problem

The Compose gitops model uses `env.template` + per-host `vars.yaml`
to support dev → staging → prod promotion. Each host renders its own
`.env` and `wikis.yaml` from shared templates with host-specific
values (secrets, URLs, ports). One repo, one branch, clean separation.

The K8s gitops model currently bakes everything into a single
`values.yaml` for a single Argo CD Application. There is no
per-environment separation. To support dev/staging/prod on K8s, we
need a repo structure that:

1. Shares the Helm chart and templates across environments
2. Allows each environment to have its own values (domains, secrets,
   image tags, resource limits, replica counts)
3. Works with Argo CD's reconciliation model
4. Supports promotion (push a change on dev, promote to staging,
   then to prod)

## Constraints

- Each environment is a separate K8s cluster (dev cluster, staging
  cluster, prod cluster)
- Each cluster has its own Argo CD instance (or a central Argo CD
  managing multiple clusters)
- Secrets must not leak between environments
- The promotion flow should be auditable (git history shows what
  changed and when)

## Options

### Option A: Per-environment branches

```
main        → dev values.yaml
staging     → staging values.yaml
prod        → prod values.yaml
```

Each Argo CD Application watches a different branch:

```yaml
# Dev Application
spec:
  source:
    repoURL: git@github.com:org/canasta-gitops.git
    targetRevision: main

# Staging Application
spec:
  source:
    targetRevision: staging

# Prod Application
spec:
  source:
    targetRevision: prod
```

**Promotion flow:**
1. Make change on `main` (dev) → push
2. Argo CD on dev cluster syncs automatically
3. When ready: `git checkout staging && git merge main && git push`
4. Argo CD on staging cluster syncs automatically
5. Same for prod

**Pros:**
- Simple Argo CD config (one field differs: `targetRevision`)
- Full git history per environment
- Can cherry-pick individual changes to staging/prod
- Each branch is a complete, self-contained state

**Cons:**
- Branch management overhead (merge conflicts if environments drift)
- Easy to forget to promote a change
- Hard to see "what's different between dev and prod" without
  diffing branches
- `canasta gitops init` would need to create and push all branches

### Option B: Per-environment directories

```
repo/
  chart/            ← shared Helm chart (Chart.yaml, templates/)
  environments/
    dev/
      values.yaml   ← dev-specific values
    staging/
      values.yaml   ← staging-specific values
    prod/
      values.yaml   ← prod-specific values
```

Each Argo CD Application points at a different path:

```yaml
# Dev Application
spec:
  source:
    path: environments/dev
    helm:
      valueFiles:
        - values.yaml
        - ../../chart/values.yaml  # base defaults

# (or use a base + overlay pattern)
```

Actually, Argo CD has a cleaner way to handle this with a shared
chart referenced by each environment:

```
repo/
  base-values.yaml     ← shared defaults
  chart/               ← shared Helm chart
  dev-values.yaml      ← dev overrides
  staging-values.yaml  ← staging overrides
  prod-values.yaml     ← prod overrides
```

```yaml
# Dev Application
spec:
  source:
    path: chart
    helm:
      valueFiles:
        - ../base-values.yaml
        - ../dev-values.yaml
```

**Promotion flow:**
1. Edit `dev-values.yaml` → push
2. Argo CD on dev cluster syncs
3. When ready: copy the change from `dev-values.yaml` to
   `staging-values.yaml` → push
4. Argo CD on staging cluster syncs
5. Same for prod

Or for changes that should apply to ALL environments (chart
templates, base values), edit the shared files and all environments
pick it up.

**Pros:**
- One branch, no merge conflicts
- Easy to diff environments: `diff dev-values.yaml prod-values.yaml`
- Shared chart changes propagate to all environments automatically
- Native Argo CD pattern (well-documented, widely used)

**Cons:**
- Promotion is manual copy between files (no merge workflow)
- All environments are visible in one repo (no access isolation)
- per-env values files can drift if someone edits prod directly

### Option C: ApplicationSets with Git Generator

Argo CD's ApplicationSet controller can generate Applications
dynamically from a directory structure:

```
repo/
  chart/
  environments/
    dev/
      values.yaml
      config.json     ← {"cluster": "dev.example.com", "namespace": "canasta-dev"}
    staging/
      values.yaml
      config.json
    prod/
      values.yaml
      config.json
```

```yaml
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: canasta
spec:
  generators:
    - git:
        repoURL: git@github.com:org/canasta-gitops.git
        directories:
          - path: environments/*
  template:
    metadata:
      name: canasta-{{path.basename}}
    spec:
      source:
        repoURL: git@github.com:org/canasta-gitops.git
        path: chart
        helm:
          valueFiles:
            - ../{{path}}/values.yaml
      destination:
        server: "{{cluster}}"
        namespace: "{{namespace}}"
```

**Promotion flow:**
Same as Option B (copy changes between per-env values files).

**Pros:**
- One ApplicationSet manages all environments (DRY)
- Adding a new environment is just adding a directory
- Argo CD's most "cloud-native" pattern

**Cons:**
- More complex Argo CD setup (ApplicationSet CRD)
- Requires understanding of generators and template syntax
- Harder to debug than individual Applications
- Overkill for 2-3 environments

### Option D: Adopt the Compose model (template + per-host vars)

Adapt the Compose gitops pattern for K8s:

```
repo/
  chart/
  values.template.yaml   ← shared, with {{placeholder}} syntax
  hosts/
    dev/
      vars.yaml           ← dev-specific values (encrypted)
    staging/
      vars.yaml
    prod/
      vars.yaml
```

`canasta gitops pull` on each environment's controller renders
`values.yaml` from `values.template.yaml` + the host's `vars.yaml`,
then runs `helm upgrade`. No Argo CD needed for the config
reconciliation — the CLI handles it.

**Promotion flow:**
1. Edit values.template.yaml or dev's vars.yaml → push
2. On staging controller: `canasta gitops pull` → renders staging's
   values.yaml → helm upgrade
3. Same for prod

**Pros:**
- Consistent model across Compose and K8s
- git-crypt encrypts per-host secrets (same as Compose)
- No Argo CD dependency for promotion
- Simple to understand if you already know the Compose flow

**Cons:**
- Loses Argo CD's continuous reconciliation (drift detection,
  selfHeal) — pull is manual, not automatic
- Each controller needs kubectl access to its cluster
- No Argo CD UI for visualizing the state
- Reinvents what Argo CD already does

### Option E: Hybrid — Compose model for config, Argo CD for drift

Combine Option D with Argo CD:

```
repo/
  chart/
  values.template.yaml
  hosts/
    dev/
      vars.yaml
      rendered-values.yaml    ← generated by pull, committed to repo
    staging/
      vars.yaml
      rendered-values.yaml
    prod/
      vars.yaml
      rendered-values.yaml
```

`canasta gitops pull` renders the values and COMMITS the rendered
file to the repo. Each environment's Argo CD Application watches
its own `rendered-values.yaml`:

```yaml
spec:
  source:
    path: chart
    helm:
      valueFiles:
        - ../hosts/staging/rendered-values.yaml
```

**Promotion flow:**
1. Edit template or dev vars → push
2. On dev controller: `canasta gitops pull` → renders
   `hosts/dev/rendered-values.yaml` → commits → pushes
3. Dev's Argo CD syncs automatically
4. On staging: `canasta gitops pull` → renders staging's file →
   pushes
5. Staging's Argo CD syncs

**Pros:**
- Best of both worlds: template + per-host vars AND Argo CD
- Each Argo CD Application has a single rendered values file (simple)
- git-crypt encrypts vars, rendered values can be unencrypted
  (Argo CD can read them)
- Consistent with Compose model
- Argo CD still does drift detection and selfHeal

**Cons:**
- Two-step promotion (pull to render, then Argo CD syncs)
- Rendered values are committed to the repo (some consider this
  an anti-pattern, but it makes the state auditable)
- Requires a controller with kubectl for each environment
  (to run pull)

## Recommendation

**Option E (Hybrid)** for the following reasons:

1. **Consistency**: Same template + vars model as Compose. Users
   learn one mental model for both orchestrators.

2. **Argo CD integration preserved**: selfHeal, drift detection,
   and the UI continue to work. The rendered-values.yaml is what
   Argo CD watches — clean, simple, one file per environment.

3. **Secrets handled correctly**: git-crypt encrypts vars.yaml
   (has secrets). rendered-values.yaml can be unencrypted because
   secrets are in K8s Secrets (not in values.yaml). Argo CD doesn't
   need git-crypt access.

4. **Auditable promotion**: Every environment's state is a committed
   file in git. `git log hosts/prod/rendered-values.yaml` shows
   exactly when and what changed in prod.

5. **Incremental implementation**: We already have the template
   rendering logic (from pull_compose.yml). The K8s version just
   renders values.yaml instead of .env. The Argo CD Application
   setup is straightforward.

## Implementation sketch for Option E

The existing `canasta gitops init/join/push/pull` commands stay the
same — the orchestrator is already known from the instance. The K8s
implementations (`init_kubernetes.yml`, `push_kubernetes.yml`, etc.)
evolve internally to support the template + per-host vars model.
The `--name` parameter that `gitops init` already takes identifies
the environment.

### `canasta gitops init` on K8s (internal changes)

```bash
canasta gitops init --id mysite --name dev \
  --repo git@github.com:org/canasta-gitops.git \
  --key ~/gitops-key
```

Creates:
```
repo/
  chart/                          ← copy of Helm chart
  values.template.yaml            ← from current values.yaml, with
                                    {{placeholders}} for per-env values
  hosts/
    hosts.yaml                    ← {canasta_id, hosts: [{name: dev, role: both}]}
    dev/
      vars.yaml                   ← extracted from current values.yaml
      rendered-values.yaml        ← rendered (same as current values.yaml initially)
  .gitattributes                  ← hosts/*/vars.yaml filter=git-crypt
  .gitignore
```

### `canasta gitops join` on K8s (internal changes)

```bash
canasta gitops join --id mysite --name staging \
  --repo git@github.com:org/canasta-gitops.git \
  --key ~/gitops-key
```

Clones, unlocks, extracts staging's values into vars.yaml, renders
staging's values.yaml, commits, pushes. Creates the Argo CD
Application pointing at `hosts/staging/rendered-values.yaml`.

### `canasta gitops pull` on K8s (multi-env)

1. `git pull`
2. Read `values.template.yaml`
3. Read `hosts/{hostname}/vars.yaml`
4. Render `hosts/{hostname}/rendered-values.yaml`
5. Commit and push (the rendered file)
6. Argo CD detects the change and syncs

### Placeholder keys for K8s values.yaml

The equivalent of Compose's env placeholder keys, but for
values.yaml fields:

| Placeholder | values.yaml path |
|---|---|
| `{{domains}}` | `domains:` |
| `{{image_tag}}` | `image.tag:` |
| `{{db_secret_name}}` | `secrets.dbSecretName:` |
| `{{mw_secret_name}}` | `secrets.mwSecretName:` |
| `{{ingress_class}}` | `ingress.className:` |
| `{{tls_enabled}}` | `ingress.tls:` |
| `{{web_replicas}}` | `web.replicaCount:` |

Everything else (chart templates, resource limits, storage classes)
can be shared across environments in the base template or overridden
per-environment in vars.yaml.

## What this means for the current implementation

The current K8s gitops (single values.yaml, no templates, no
per-env vars) can continue to work for single-environment setups.
The multi-env support would be a new mode activated when `--name`
is provided to `gitops init` on K8s.

The Argo CD Application creation would change from pointing at the
repo root to pointing at the chart directory with a per-env
valueFiles reference. Existing single-env setups would be
unaffected.
