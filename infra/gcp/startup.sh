#!/usr/bin/env bash
# Runs as root at every boot of the Mentor server (create-server.sh sets it as the startup script).
# First boot adds swap and installs Docker; later boots change nothing.
set -euo pipefail

# 1 GB of memory is tight for Postgres + the bot; swap absorbs spikes instead of a crash.
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  echo "/swapfile none swap sw 0 0" >> /etc/fstab
fi
swapon --show | grep -q /swapfile || swapon /swapfile

if ! command -v docker >/dev/null; then
  apt-get update
  apt-get install -y ca-certificates curl unattended-upgrades
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  # shellcheck source=/dev/null
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc]" \
    "https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

# The bot's container runs as uid 10001 and keeps the Google sign-in in secrets/.
install -d -m 755 /opt/mentor
install -d -m 700 -o 10001 -g 10001 /opt/mentor/secrets
