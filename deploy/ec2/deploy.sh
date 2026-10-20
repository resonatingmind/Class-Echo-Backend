#!/bin/bash
# Pull the latest code and (re)start the stack. Safe to run repeatedly.
# Run from anywhere:   bash deploy/ec2/deploy.sh
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DEPLOY_DIR"

if [ ! -f .env ]; then
    echo "Missing $DEPLOY_DIR/.env — run bootstrap.sh first." >&2
    exit 1
fi
if grep -Eq '^CLOUDINARY_[A-Z_]+=.+' .env; then
    echo "WARNING: CLOUDINARY_* is set in .env, so uploads go to Cloudinary, not S3." >&2
fi
if grep -Eq '^(RESET_DB_ON_STARTUP=true|ENVIRONMENT=test)' .env; then
    echo "Refusing to deploy: RESET_DB_ON_STARTUP/ENVIRONMENT=test would wipe the database." >&2
    exit 1
fi

if git -C "$DEPLOY_DIR" rev-parse --git-dir >/dev/null 2>&1; then
    echo "==> git pull"
    git -C "$DEPLOY_DIR" pull --ff-only
fi

echo "==> Building and starting containers"
docker compose up -d --build --remove-orphans

echo "==> Waiting for API health check"
for i in $(seq 1 40); do
    status="$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q api)" 2>/dev/null || true)"
    if [ "$status" = "healthy" ]; then
        echo "    API is healthy."
        docker compose ps
        docker image prune -f >/dev/null
        exit 0
    fi
    sleep 5
done

echo "API did not become healthy in time. Recent logs:" >&2
docker compose logs --tail 80 api >&2
exit 1
