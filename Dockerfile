FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    git-crypt \
    openssh-client \
    rsync \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

# Install docker compose plugin
RUN mkdir -p /usr/local/lib/docker/cli-plugins \
    && ARCH=$(dpkg --print-architecture) \
    && curl -fsSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-${ARCH}" \
         -o /usr/local/lib/docker/cli-plugins/docker-compose \
    && chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# Copy application
WORKDIR /opt/canasta-ansible
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Make wrapper executable
RUN chmod +x canasta

ENTRYPOINT ["/opt/canasta-ansible/canasta"]
