# Fix ansible-galaxy collection install hang in Dockerfile

## TL;DR

> **Quick Summary**: Replace the hanging `ansible-galaxy collection install` command with a direct `curl` download of the kubernetes.core collection tarball + local install, consistent with how Docker CLI, kubectl, and Helm are already installed in this Dockerfile.
> 
> **Deliverables**:
> - Updated `requirements.yml` with pinned kubernetes.core version
> - Updated `Dockerfile` replacing ansible-galaxy network install with curl download + local install
> 
> **Estimated Effort**: Quick
> **Parallel Execution**: NO - sequential (single task)
> **Critical Path**: Task 1 → F1-F4

---

## Context

### Original Request
The Dockerfile hangs during `ansible-galaxy -vvv collection install -r requirements.yml`. Even with `--progress=plain`, `--log-level=debug`, and `| cat` piped output, no ansible-galaxy output appears. The last visible output is buildah runtime debug messages ending with `"closing stdin"`.

### Interview Summary
**Key Discussions**:
- Initial investigation focused on pip install hang; user applied `--only-binary :all:` and split pip/ansible-galaxy into separate RUN layers
- pip install is slow but completes; the real hang is ansible-galaxy
- User confirmed podman/buildah is the build tool
- `--progress=plain` and `--log-level=debug` show buildah runtime messages but NOT ansible-galaxy output
- The `| cat` pipe trick was tried but didn't help

**Root Cause Analysis**:
1. `ansible-galaxy collection install` downloads the kubernetes.core collection from `galaxy.ansible.com` via its own HTTP client
2. The Galaxy server can be slow or the connection can stall, and `--timeout 60` may not cover all phases of the download
3. buildah/podman swallows stdout/stderr from the container process in certain conditions, so even `-vvv` output never appears
4. The `>=6.4.0` version constraint in `requirements.yml` forces ansible-galaxy to query the Galaxy API for available versions before downloading, adding another network round-trip that can hang

### Research Findings
- Ansible Galaxy API v2 provides direct tarball download at: `https://galaxy.ansible.com/api/v2/collections/kubernetes/core/versions/{version}/download/`
- `ansible-galaxy collection install` supports installing from a local tarball file: `ansible-galaxy collection install /path/to/collection.tar.gz`
- The rest of this Dockerfile already uses `curl --retry 3 --retry-delay 5` for all network downloads (Docker CLI, Compose, kubectl, Helm) — this pattern should be applied to the collection download too
- Latest kubernetes.core version is 6.5.0 (confirmed via Galaxy)

---

## Work Objectives

### Core Objective
Make the Dockerfile build reliably and quickly by replacing ansible-galaxy's network-dependent collection install with a curl-based direct download + local install, matching the existing Dockerfile patterns.

### Concrete Deliverables
- `requirements.yml` — pin `kubernetes.core` to version `6.5.0` (exact, not `>=`)
- `Dockerfile` — replace the `ansible-galaxy collection install -r requirements.yml` RUN with a curl download + local ansible-galaxy install

### Definition of Done
- [x] `podman build --progress=plain .` completes without hanging on the collection install step
- [x] curl download shows visible progress output during build
- [x] Collection is installed at `/usr/share/ansible/collections/ansible_collections/kubernetes/core/`

### Must Have
- curl with `--retry 3 --retry-delay 5 --connect-timeout 30 --max-time 120` (consistent with existing Dockerfile patterns, plus explicit timeouts)
- Pinned kubernetes.core version (exact, not range) to avoid Galaxy API version-resolution queries
- Collection installed to the same path: `/usr/share/ansible/collections`
- `--no-deps` flag on the local install (kubernetes.core has no collection dependencies; avoids any further network calls)

### Must NOT Have (Guardrails)
- Do NOT remove the `--only-binary :all:` flag from pip install (user already applied this)
- Do NOT re-merge pip install and ansible-galaxy into one RUN layer (user intentionally split them for caching)
- Do NOT add `ansible-lint`, `yamllint`, or `ruff` to the Docker image beyond what pip already installs (they're dev tools, not needed at runtime — but they're in requirements.txt so leave as-is)
- Do NOT change any other RUN commands (Docker CLI, Compose, kubectl, Helm installs)
- Do NOT add `| cat` or other output-piping hacks — curl provides its own progress output

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** - ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: NO (this is a Dockerfile change, not application code)
- **Automated tests**: None
- **Framework**: N/A

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **Build verification**: Use Bash (podman build) - Build the image, verify it completes, verify the collection is installed
- **Runtime verification**: Use Bash (podman run) - Run the image and verify ansible can find the kubernetes.core collection

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Single task - sequential):
└── Task 1: Replace ansible-galaxy network install with curl download + local install

Wave FINAL (After Task 1 — 4 parallel reviews):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Real manual QA (unspecified-high)
└── Task F4: Scope fidelity check (deep)
-> Present results -> Get explicit user okay
```

### Dependency Matrix

| Task | Depends On | Blocks |
|------|-----------|--------|
| 1    | —         | F1-F4  |
| F1-F4| 1         | —      |

### Agent Dispatch Summary

- **Wave 1**: **1** — T1 → `quick`
- **FINAL**: **4** — F1 → `oracle`, F2 → `unspecified-high`, F3 → `unspecified-high`, F4 → `deep`

---

## TODOs

- [x] 1. Replace ansible-galaxy network install with curl download + local install

  **What to do**:
  - In `requirements.yml`: Change `version: ">=6.4.0"` to `version: "6.5.0"` (exact pin, remove the `>=`). Update the comment to note the pin is also for build reliability (avoids Galaxy API version-resolution network calls that can hang).
  - In `Dockerfile`: Replace line 41:
    ```dockerfile
    RUN ansible-galaxy -vvv collection install -r requirements.yml -p /usr/share/ansible/collections --timeout 60 | cat
    ```
    with a two-step approach:
    ```dockerfile
    # Download kubernetes.core collection tarball directly (avoids ansible-galaxy
    # network hangs during build; consistent with curl pattern used for Docker,
    # kubectl, and Helm above). Pinned version must match requirements.yml.
    RUN K8S_CORE_VERSION="6.5.0" && \
        curl -fsSL --retry 3 --retry-delay 5 --connect-timeout 30 --max-time 120 \
             "https://galaxy.ansible.com/api/v2/collections/kubernetes/core/versions/${K8S_CORE_VERSION}/download/" \
             -o /tmp/kubernetes-core.tar.gz && \
        ansible-galaxy collection install /tmp/kubernetes-core.tar.gz \
             -p /usr/share/ansible/collections --no-deps && \
        rm -f /tmp/kubernetes-core.tar.gz
    ```
  - The `--no-deps` flag tells ansible-galaxy not to resolve/download any collection dependencies (kubernetes.core has no collection dependencies, only Python dependencies which are already in requirements.txt).
  - The `--connect-timeout 30` fails fast if the Galaxy server is unreachable. The `--max-time 120` caps the total download time.
  - curl's `-f` flag fails the build on HTTP errors (404, 500, etc.) instead of silently writing an error page to the tarball.
  - Keep the `requirements.yml` file in place (it's still COPYed into the image and may be referenced by ansible at runtime for inventory purposes), but it's no longer used by the build-time ansible-galaxy command.

  **Must NOT do**:
  - Do NOT remove `requirements.yml` from the `COPY requirements.txt requirements.yml ./` line (line 39) — it may be needed at runtime
  - Do NOT add `| cat` or other piping tricks — curl provides visible progress
  - Do NOT change the pip install line (line 40) — user already optimized it
  - Do NOT merge this RUN with the pip install RUN — keep them separate for layer caching

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Single-file Dockerfile edit + single-line requirements.yml edit. Mechanical change with clear instructions.
  - **Skills**: `[]`
    - No specialized skills needed for a Dockerfile edit.

  **Parallelization**:
  - **Can Run In Parallel**: NO (only one task)
  - **Parallel Group**: Wave 1 (alone)
  - **Blocks**: F1, F2, F3, F4
  - **Blocked By**: None (can start immediately)

  **References**:

  **Pattern References** (existing code to follow):
  - `Dockerfile:14-16` — Docker CLI download pattern: `curl -fsSL --retry 3 --retry-delay 5 ... | tar xz` — use this same curl flag pattern for the collection download
  - `Dockerfile:27-30` — kubectl download pattern: `curl -fsSL --retry 3 --retry-delay 5 ... -o /usr/local/bin/kubectl && chmod +x` — use this same curl-to-file pattern
  - `Dockerfile:40` — pip install line — DO NOT modify, just keep as context for what precedes the changed line

  **API/Type References**:
  - `requirements.yml:7-8` — Current kubernetes.core entry with `version: ">=6.4.0"` — this is what needs to be pinned to `"6.5.0"`
  - `Dockerfile:41` — Current ansible-galaxy command — this is what needs to be replaced

  **External References**:
  - Ansible Galaxy API v2 download endpoint: `https://galaxy.ansible.com/api/v2/collections/kubernetes/core/versions/{version}/download/` — returns the collection tarball (redirects to CDN)
  - ansible-galaxy collection install from local file: `ansible-galaxy collection install /path/to/collection.tar.gz` — documented at https://docs.ansible.com/projects/ansible/latest/collections_guide/collections_installing.html
  - kubernetes.core Galaxy page: https://galaxy.ansible.com/ui/repo/published/kubernetes/core — confirms latest version is 6.5.0

  **WHY Each Reference Matters**:
  - The curl patterns in the existing Dockerfile establish the convention: `--retry 3 --retry-delay 5` for resilience, `-fsSL` for fail-fast + follow redirects. The new download should match this exactly, plus add `--connect-timeout` and `--max-time` since the collection tarball is larger than the CLI binaries.
  - The `requirements.yml` version pin matters because `ansible-galaxy` with `>=6.4.0` makes an API call to Galaxy to resolve available versions — that API call is itself a network round-trip that can hang. Pinning to exact `6.5.0` and downloading directly via curl bypasses the Galaxy API entirely.
  - The `--no-deps` flag matters because without it, ansible-galaxy may attempt to resolve and download collection dependencies from Galaxy — re-introducing the network hang. kubernetes.core has no collection dependencies (its dependencies are Python packages, already in requirements.txt).

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: Dockerfile build completes without hanging
    Tool: Bash (podman)
    Preconditions: Working directory is /home/mah/work/code/Canasta-CLI, podman is installed
    Steps:
      1. Run: podman build --progress=plain --no-cache -t canasta-cli-test . 2>&1 | tee /tmp/build-output.log
      2. Monitor the build output — look for the curl download line showing progress (e.g., "Downloading" or percentage progress)
      3. Wait for the build to complete (should finish within 5 minutes for the collection install step, not hang indefinitely)
      4. Check exit code: echo $? — should be 0
      5. Grep the build log for "curl" near the collection download step to confirm curl ran: grep -c "curl" /tmp/build-output.log
    Expected Result: Build completes with exit code 0. The collection install step shows curl progress output and completes within ~2 minutes (download + local install). No hanging.
    Failure Indicators: Build hangs for more than 5 minutes on the collection install step. Exit code non-zero. curl shows HTTP error (404, 500). No curl output visible in build log.
    Evidence: .sisyphus/evidence/task-1-build-completes.txt (the full build log)

  Scenario: kubernetes.core collection is installed in the image
    Tool: Bash (podman)
    Preconditions: Image tagged as canasta-cli-test from previous scenario
    Steps:
      1. Run: podman run --rm canasta-cli-test ansible-galaxy collection list 2>&1
      2. Grep output for "kubernetes.core" — should show version 6.5.0
      3. Run: podman run --rm canasta-cli-test ls /usr/share/ansible/collections/ansible_collections/kubernetes/core/ — should list collection files (plugins/, meta/, galaxy.yml, etc.)
    Expected Result: "kubernetes.core" appears in the collection list with version 6.5.0. The collection directory exists with expected files.
    Failure Indicators: "kubernetes.core" not found in collection list. Directory does not exist. Version is not 6.5.0.
    Evidence: .sisyphus/evidence/task-1-collection-installed.txt (output of both commands)

  Scenario: Network failure fails fast instead of hanging
    Tool: Bash (podman)
    Preconditions: Working directory is /home/mah/work/code/Canasta-CLI
    Steps:
      1. Temporarily break the download URL by editing the Dockerfile to use a non-existent version (e.g., K8S_CORE_VERSION="0.0.0")
      2. Run: timeout 60 podman build --progress=plain --no-cache -t canasta-cli-failtest . 2>&1
      3. Check: the build should FAIL within 60 seconds (curl gets 404, -f flag causes exit, RUN fails)
      4. Revert the Dockerfile change (restore K8S_CORE_VERSION="6.5.0")
    Expected Result: Build fails fast with a curl HTTP error (404) within the 60-second timeout. Does NOT hang.
    Failure Indicators: Build hangs past 60 seconds. Build succeeds (shouldn't with invalid version).
    Evidence: .sisyphus/evidence/task-1-fail-fast.txt (output showing fast failure)
  ```

  **Evidence to Capture**:
  - [x] `task-1-build-completes.txt` — full build log showing successful completion
  - [x] `task-1-collection-installed.txt` — ansible-galaxy collection list output + directory listing
  - [x] `task-1-fail-fast.txt` — output showing fast failure on invalid version

  **Commit**: YES
  - Message: `fix(docker): replace ansible-galaxy network install with curl download to prevent build hang`
  - Files: `Dockerfile`, `requirements.yml`
  - Pre-commit: `podman build --progress=plain .` (verify build succeeds)

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.

- [x] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read Dockerfile, check curl flags, check version pin in requirements.yml). For each "Must NOT Have": search Dockerfile for forbidden patterns (`| cat`, merged RUN, removed `--only-binary`). Check evidence files exist in .sisyphus/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [x] F2. **Code Quality Review** — `unspecified-high`
  Review the changed Dockerfile and requirements.yml for: syntax errors, inconsistent curl flags vs existing patterns, missing `--no-deps`, version pin format correctness. Check that the curl URL matches the Galaxy API v2 format. Verify no AI slop: excessive comments, unnecessary variables.
  Output: `Dockerfile [CLEAN/N issues] | requirements.yml [CLEAN/N issues] | VERDICT`

- [x] F3. **Real Manual QA** — `unspecified-high`
  Start from clean state. Run `podman build --progress=plain --no-cache .` and verify ALL 3 QA scenarios from Task 1 pass. Test: build completes, collection installed, fail-fast on bad version. Save evidence to `.sisyphus/evidence/final-qa/`.
  Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`

- [x] F4. **Scope Fidelity Check** — `deep`
  Read "What to do" from Task 1, read actual git diff. Verify 1:1 — only Dockerfile line 41 and requirements.yml version were changed. No other lines touched. Check "Must NOT do" compliance: `--only-binary` still present, pip/ansible-galaxy still separate RUN layers, no `| cat`.
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

- **1**: `fix(docker): replace ansible-galaxy network install with curl download to prevent build hang` — `Dockerfile`, `requirements.yml`
  - Pre-commit: `podman build --progress=plain .`

---

## Success Criteria

### Verification Commands
```bash
# Build completes without hanging (should finish in < 5 min for collection step)
podman build --progress=plain --no-cache -t canasta-cli-test .

# Collection is installed and visible
podman run --rm canasta-cli-test ansible-galaxy collection list | grep "kubernetes.core"
# Expected: kubernetes.core  6.5.0

# Collection directory exists
podman run --rm canasta-cli-test ls /usr/share/ansible/collections/ansible_collections/kubernetes/core/
# Expected: plugins/ meta/ galaxy.yml etc.
```

### Final Checklist
- [x] All "Must Have" present (curl with retries/timeouts, pinned version, --no-deps, correct install path)
- [x] All "Must NOT Have" absent (--only-binary still present, separate RUN layers, no | cat)
- [x] Build completes without hanging
- [x] Collection installed at correct path with correct version
- [x] Network failure fails fast (curl timeout/404) instead of hanging
