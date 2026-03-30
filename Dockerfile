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

# Install Docker Compose plugin
RUN mkdir -p /usr/local/lib/docker/cli-plugins \
    && ARCH=$(dpkg --print-architecture) \
    && curl -fsSL \
         "https://github.com/docker/compose/releases/download/v2.35.1/docker-compose-linux-${ARCH}" \
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
