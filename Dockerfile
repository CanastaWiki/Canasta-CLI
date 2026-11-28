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

# Copy application
WORKDIR /opt/canasta-ansible
COPY requirements.txt requirements.yml ./
# --only-binary: never build from source — this image has no compiler,
# and cryptography/cffi/rpds-py et al. would need one. Requires a glibc
# base and a Python version all pinned wheels publish for.
RUN pip install --only-binary :all: --no-cache-dir -r requirements.txt --root-user-action=ignore
# Download collections directly via curl (avoids ansible-galaxy network hangs
# under podman/buildah; consistent with the curl pattern used for Docker
# above). Pinned versions must match requirements.yml.
RUN ANSIBLE_POSIX_VERSION="2.2.2" && \
    curl -fsSL --retry 3 --retry-delay 5 --retry-all-errors --retry-connrefused --connect-timeout 30 --max-time 120 \
         "https://galaxy.ansible.com/download/ansible-posix-${ANSIBLE_POSIX_VERSION}.tar.gz" \
         -o /tmp/ansible-posix.tar.gz && \
    ansible-galaxy collection install /tmp/ansible-posix.tar.gz \
         -p /usr/share/ansible/collections --no-deps && \
    rm -f /tmp/ansible-posix.tar.gz

COPY . .

# Make wrapper executable
RUN chmod +x canasta-native

# Build metadata (injected by CI)
ARG BUILD_COMMIT=unknown
ARG BUILD_DATE=unknown
RUN echo "$BUILD_COMMIT" > /opt/canasta-ansible/BUILD_COMMIT \
    && echo "$BUILD_DATE" > /opt/canasta-ansible/BUILD_DATE

ENTRYPOINT ["/opt/canasta-ansible/canasta-native"]
