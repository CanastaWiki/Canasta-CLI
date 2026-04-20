FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Install SSH server, Python 3, sudo, cron (for backup schedule tests),
# and prerequisites for Docker CLI
RUN apt-get update && apt-get install -y --no-install-recommends \
        openssh-server \
        python3 \
        python3-yaml \
        sudo \
        cron \
        ca-certificates \
        curl \
        gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install Docker CLI (not the daemon -- we mount the host socket)
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
       | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
       https://download.docker.com/linux/ubuntu noble stable" \
       > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

# Create testuser with sudo (no password)
RUN useradd -m -s /bin/bash testuser \
    && echo "testuser ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

# Prepare SSH
RUN mkdir /run/sshd \
    && mkdir -p /home/testuser/.ssh \
    && chmod 700 /home/testuser/.ssh \
    && chown testuser:testuser /home/testuser/.ssh

# Prepare Canasta config directory
RUN mkdir -p /etc/canasta \
    && chown testuser:testuser /etc/canasta

EXPOSE 22

CMD ["/usr/sbin/sshd", "-D", "-e"]
