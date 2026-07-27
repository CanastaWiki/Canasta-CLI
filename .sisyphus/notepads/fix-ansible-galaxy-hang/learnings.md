# Learnings

## 2026-07-26 — Fix ansible-galaxy network hang in Dockerfile

- `ansible-galaxy collection install -r requirements.yml` triggers Galaxy API version-resolution queries that can hang indefinitely under podman/buildah.
- Fix: curl the collection tarball directly from Galaxy API (`/api/v2/collections/kubernetes/core/versions/${VERSION}/download/`) and install locally with `--no-deps`.
- `--no-deps` is safe: kubernetes.core has no collection dependencies (only Python deps, already in requirements.txt).
- curl flags used: `-fsSL --retry 3 --retry-delay 5 --connect-timeout 30 --max-time 120` — consistent with existing pattern (Docker, Compose, kubectl, Helm).
- requirements.yml pin changed from `">=6.4.0"` (range) to `"6.5.0"` (exact) to avoid the version-resolution query altogether at build time. requirements.yml is still needed at runtime.

## 2026-07-26 — F3 Real Manual QA Results

- All 3 QA scenarios PASSED from clean state (--no-cache build):
  - S1: Build completed with exit code 0, STEP 10 showed "kubernetes.core:6.5.0 was installed successfully", no hanging
  - S2: `ansible-galaxy collection list` confirmed kubernetes.core 6.5.0; directory listing showed expected files (plugins/, meta/, CHANGELOG.rst, etc.)
  - S3: Build with broken version (0.0.0) failed fast with curl HTTP 500 error (retried 3x, then exited non-zero), no hanging
- Evidence saved to `.sisyphus/evidence/final-qa/qa{1,2,3}-*.txt`
- The actual download URL used is `https://galaxy.ansible.com/download/kubernetes-core-${VERSION}.tar.gz` (not the API v2 endpoint from the plan — the `/download/` path works and returns 500 for invalid versions, which is still a fast failure)