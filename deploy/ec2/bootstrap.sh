#!/bin/bash
# One-time setup of a fresh Ubuntu 22.04/24.04 EC2 instance.
# Run from deploy/ec2:   sudo bash bootstrap.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo: sudo bash bootstrap.sh" >&2
    exit 1
fi

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET_USER="${SUDO_USER:-ubuntu}"

echo "==> Installing Docker"
if ! command -v docker >/dev/null 2>&1; then
    apt-get update
    apt-get install -y ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker
usermod -aG docker "$TARGET_USER"

echo "==> Adding 4 GB swap (safety net for the worker and image builds)"
if ! swapon --show | grep -q '/swapfile'; then
    fallocate -l 4G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    sysctl -w vm.swappiness=10
    echo 'vm.swappiness=10' > /etc/sysctl.d/99-swappiness.conf
fi

echo "==> Creating .env"
if [ ! -f "$DEPLOY_DIR/.env" ]; then
    cp "$DEPLOY_DIR/.env.example" "$DEPLOY_DIR/.env"
    sed -i "s|^SECRET_KEY=$|SECRET_KEY=$(openssl rand -hex 32)|" "$DEPLOY_DIR/.env"
    chown "$TARGET_USER":"$TARGET_USER" "$DEPLOY_DIR/.env"
    chmod 600 "$DEPLOY_DIR/.env"
    echo "    Created $DEPLOY_DIR/.env with a generated SECRET_KEY."
else
    echo "    $DEPLOY_DIR/.env already exists, left untouched."
fi

echo
echo "Done. Next:"
echo "  1. Log out and back in (so '$TARGET_USER' can run docker without sudo)"
echo "  2. Edit $DEPLOY_DIR/.env  (DATABASE_URL, MIGRATION_DATABASE_URL, GEMINI_API_KEY, storage, email, FRONTEND_URL)"
echo "  3. bash $DEPLOY_DIR/deploy.sh"
