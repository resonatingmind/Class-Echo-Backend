#!/bin/bash
set -euo pipefail

# Wait for the database to accept connections before running migrations.
# Without this, alembic/seed can race a not-yet-ready Postgres container
# and fail silently (start.sh had no `set -e`), leaving the app serving
# against an empty, unmigrated database.
python - <<'PY'
import os
import sys
import time

import psycopg2

url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")

for attempt in range(30):
    try:
        conn = psycopg2.connect(url)
        conn.close()
        break
    except psycopg2.OperationalError as e:
        print(f"  [wait-for-db] not ready yet ({e}); retrying ({attempt + 1}/30) ...")
        time.sleep(2)
else:
    print("  [wait-for-db] database never became ready", file=sys.stderr)
    sys.exit(1)
PY

# Wipe the database first if requested
if [ "${RESET_DB_ON_DEPLOY:-false}" = "true" ]; then
    echo "RESET_DB_ON_DEPLOY=true -- wiping database schema completely"
    python reset_db.py --yes
fi

# Run database migrations
alembic upgrade head

# Seed the database.
python seed_admin.py

# Start celery worker in the background
celery -A celery_worker.celery_app worker --loglevel=info --concurrency=1 &
# Start uvicorn
uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
