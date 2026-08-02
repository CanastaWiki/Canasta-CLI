FROM python:3.12-slim

# Several RUN steps below pipe a download straight into tar or bash.
# Without pipefail a failed or truncated download still leaves the
# pipeline exiting 0 — the Helm step in particular would pipe nothing
# into bash and the image would build "successfully" with no helm in it.
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

# Install Docker CLI (not daemon - we use host's Docker via socket)
RUN curl -fsSL --retry 3 --retry-delay 5 \
        "https://download.docker.com/linux/static/stable/$(uname -m)/docker-27.5.1.tgz" \
    | tar xz --strip-components=1 -C /usr/local/bin docker/docker

# Install Docker Compose plugin (uses uname -m for arch: x86_64/aarch64)
RUN mkdir -p /usr/local/lib/docker/cli-plugins \
    && curl -fsSL --retry 3 --retry-delay 5 \
         "https://github.com/docker/compose/releases/download/v5.1.1/docker-compose-linux-$(uname -m)" \
         -o /usr/local/lib/docker/cli-plugins/docker-compose \
    && chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# Install kubectl (latest stable). dpkg --print-architecture maps to
# amd64 / arm64, matching the directories under dl.k8s.io. See #62.
RUN curl -fsSL --retry 3 --retry-delay 5 \
        "https://dl.k8s.io/release/$(curl -fsSL --retry 3 https://dl.k8s.io/release/stable.txt)/bin/linux/$(dpkg --print-architecture)/kubectl" \
        -o /usr/local/bin/kubectl \
    && chmod +x /usr/local/bin/kubectl

# Install Helm (3.x via the official installer script).
RUN curl -fsSL --retry 3 --retry-delay 5 \
        https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 \
    | bash

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
    curl -fsSL --retry 3 --retry-delay 5 --connect-timeout 30 --max-time 120 \
         "https://galaxy.ansible.com/download/kubernetes-core-${K8S_CORE_VERSION}.tar.gz" \
         -o /tmp/kubernetes-core.tar.gz && \
    curl -fsSL --retry 3 --retry-delay 5 --connect-timeout 30 --max-time 120 \
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
