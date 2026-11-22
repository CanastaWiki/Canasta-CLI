FROM python:3.12-slim

# Several RUN steps below pipe a download straight into tar or bash.
# Without pipefail a failed or truncated download still leaves the
# pipeline exiting 0.
# python:3.12-slim is Debian-based, so bash is present.
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    git-crypt \
    openssh-client \
    rsync \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Plain --retry covers HTTP 5xx, 408, 429 and timeouts only. A connection
# reset or TLS-level failure is outside that set, so every download below
# also carries --retry-all-errors and --retry-connrefused.
# Install Docker CLI (not daemon - we use host's Docker via socket)
RUN curl -fsSL --retry 3 --retry-delay 5 --retry-all-errors --retry-connrefused \
        "https://download.docker.com/linux/static/stable/$(uname -m)/docker-27.5.1.tgz" \
    | tar xz --strip-components=1 -C /usr/local/bin docker/docker

# Install Docker Compose plugin (uses uname -m for arch: x86_64/aarch64)
RUN mkdir -p /usr/local/lib/docker/cli-plugins \
    && curl -fsSL --retry 3 --retry-delay 5 --retry-all-errors --retry-connrefused \
         "https://github.com/docker/compose/releases/download/v5.1.1/docker-compose-linux-$(uname -m)" \
         -o /usr/local/lib/docker/cli-plugins/docker-compose \
    && chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# Install kubectl and Helm, pinned with checksums. kubectl tracks the minor of
# canasta_k3s_version and Helm matches canasta_helm_version
# (roles/orchestrator/defaults/main.yml); bump them together.
# dpkg --print-architecture maps to amd64 / arm64, matching both release URLs.
ARG KUBECTL_VERSION=v1.34.8
ARG KUBECTL_SHA256_AMD64=f6249132865c13abe3c9dd5038f5da65849cb86eee1608c001831504e481aa8c
ARG KUBECTL_SHA256_ARM64=4c9fe1f717738950c638c38056130a8db5075e6413ae36d8687221a240cdf88b
ARG HELM_VERSION=v3.20.1
ARG HELM_SHA256_AMD64=0165ee4a2db012cc657381001e593e981f42aa5707acdd50658326790c9d0dc3
ARG HELM_SHA256_ARM64=56b9d1b0e0efbb739be6e68a37860ace8ec9c7d3e6424e3b55d4c459bc3a0401
RUN set -e; arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) kubectl_sha="$KUBECTL_SHA256_AMD64"; helm_sha="$HELM_SHA256_AMD64" ;; \
      arm64) kubectl_sha="$KUBECTL_SHA256_ARM64"; helm_sha="$HELM_SHA256_ARM64" ;; \
      *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL --retry 3 --retry-delay 5 --retry-all-errors --retry-connrefused \
        "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${arch}/kubectl" \
        -o /usr/local/bin/kubectl; \
    echo "${kubectl_sha}  /usr/local/bin/kubectl" | sha256sum -c -; \
    chmod +x /usr/local/bin/kubectl; \
    curl -fsSL --retry 3 --retry-delay 5 --retry-all-errors --retry-connrefused \
        "https://get.helm.sh/helm-${HELM_VERSION}-linux-${arch}.tar.gz" \
        -o /tmp/helm.tar.gz; \
    echo "${helm_sha}  /tmp/helm.tar.gz" | sha256sum -c -; \
    tar xzf /tmp/helm.tar.gz --strip-components=1 -C /usr/local/bin "linux-${arch}/helm"; \
    rm /tmp/helm.tar.gz

# Copy application
WORKDIR /opt/canasta-ansible
COPY requirements.txt requirements.yml ./
# --only-binary: never build from source — this image has no compiler,
# and cryptography/cffi/rpds-py et al. would need one. Requires a glibc
# base and a Python version all pinned wheels publish for.
RUN pip install --only-binary :all: --no-cache-dir -r requirements.txt --root-user-action=ignore
# Download collections directly via curl (avoids ansible-galaxy network hangs
# under podman/buildah; consistent with curl pattern used for Docker, kubectl,
# and Helm above). Pinned versions must match requirements.yml.
RUN K8S_CORE_VERSION="6.5.0" && \
    ANSIBLE_POSIX_VERSION="2.2.2" && \
    curl -fsSL --retry 3 --retry-delay 5 --retry-all-errors --retry-connrefused --connect-timeout 30 --max-time 120 \
         "https://galaxy.ansible.com/download/kubernetes-core-${K8S_CORE_VERSION}.tar.gz" \
         -o /tmp/kubernetes-core.tar.gz && \
    curl -fsSL --retry 3 --retry-delay 5 --retry-all-errors --retry-connrefused --connect-timeout 30 --max-time 120 \
         "https://galaxy.ansible.com/download/ansible-posix-${ANSIBLE_POSIX_VERSION}.tar.gz" \
         -o /tmp/ansible-posix.tar.gz && \
    ansible-galaxy collection install /tmp/kubernetes-core.tar.gz \
         -p /usr/share/ansible/collections --no-deps && \
    ansible-galaxy collection install /tmp/ansible-posix.tar.gz \
         -p /usr/share/ansible/collections --no-deps && \
    rm -f /tmp/kubernetes-core.tar.gz /tmp/ansible-posix.tar.gz

COPY . .

# Make wrapper executable
RUN chmod +x canasta-native

# Build metadata (injected by CI)
ARG BUILD_COMMIT=unknown
ARG BUILD_DATE=unknown
RUN echo "$BUILD_COMMIT" > /opt/canasta-ansible/BUILD_COMMIT \
    && echo "$BUILD_DATE" > /opt/canasta-ansible/BUILD_DATE

ENTRYPOINT ["/opt/canasta-ansible/canasta-native"]
