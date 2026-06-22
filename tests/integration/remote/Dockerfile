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

# Create testuser as a faithful canasta account — a member of every group
# 'canasta doctor' expects: www-data (Canasta containers write files as
# www-data) and canasta (upgrade self-update), plus docker. www-data MUST
# be gid 33 to match the Canasta web image's www-data, which chowns the
# bind-mounted config/; otherwise testuser can't write the group-writable
# config files (e.g. config/backup-schedule.yml written by 'backup
# schedule set'). Passwordless sudo for install steps.
RUN groupadd -f -g 33 www-data \
    && groupadd -f canasta \
    && groupadd -f docker \
    && useradd -m -s /bin/bash -G www-data,canasta,docker testuser \
    && echo "testuser ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

# Prepare SSH
RUN mkdir /run/sshd \
    && mkdir -p /home/testuser/.ssh \
    && chmod 700 /home/testuser/.ssh \
    && chown testuser:testuser /home/testuser/.ssh

# Prepare Canasta config directory
RUN mkdir -p /etc/canasta \
    && chown testuser:testuser /etc/canasta

# Stub 'canasta' on PATH. 'canasta backup schedule set' preflights that a
# runnable canasta exists on the host (scheduled backups run 'canasta
# backup create' there via crontab) and bakes its absolute path into the
# cron entry. The schedule tests only verify the crontab is written and
# read back, not that a backup runs, so a no-op stub satisfies the
# preflight without installing the full CLI on the test host.
RUN printf '#!/bin/sh\nexit 0\n' > /usr/local/bin/canasta \
    && chmod 0755 /usr/local/bin/canasta

EXPOSE 22

CMD ["/usr/sbin/sshd", "-D", "-e"]
