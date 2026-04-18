FROM python:3.12-slim

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
RUN curl -fsSL https://download.docker.com/linux/static/stable/$(uname -m)/docker-27.5.1.tgz \
    | tar xz --strip-components=1 -C /usr/local/bin docker/docker

# Install Docker Compose plugin (uses uname -m for arch: x86_64/aarch64)
RUN mkdir -p /usr/local/lib/docker/cli-plugins \
    && curl -fsSL \
         "https://github.com/docker/compose/releases/download/v5.1.1/docker-compose-linux-$(uname -m)" \
         -o /usr/local/lib/docker/cli-plugins/docker-compose \
    && chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# Install kubectl (latest stable). dpkg --print-architecture maps to
# amd64 / arm64, matching the directories under dl.k8s.io. See #62.
RUN curl -fsSL \
        "https://dl.k8s.io/release/$(curl -fsSL https://dl.k8s.io/release/stable.txt)/bin/linux/$(dpkg --print-architecture)/kubectl" \
        -o /usr/local/bin/kubectl \
    && chmod +x /usr/local/bin/kubectl

# Install Helm (3.x via the official installer script).
RUN curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 \
        | bash

# Copy application
WORKDIR /opt/canasta-ansible
COPY requirements.txt requirements.yml ./
RUN pip install --no-cache-dir -r requirements.txt \
    && ansible-galaxy collection install -r requirements.yml -p /usr/share/ansible/collections

COPY . .

# Make wrapper executable
RUN chmod +x canasta-native

# Build metadata (injected by CI)
ARG BUILD_COMMIT=unknown
ARG BUILD_DATE=unknown
RUN echo "$BUILD_COMMIT" > /opt/canasta-ansible/BUILD_COMMIT \
    && echo "$BUILD_DATE" > /opt/canasta-ansible/BUILD_DATE

ENTRYPOINT ["/opt/canasta-ansible/canasta-native"]
