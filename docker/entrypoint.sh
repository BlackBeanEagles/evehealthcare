#!/usr/bin/env sh
# Wait for the database, apply migrations, optionally seed, then run the
# command. Migrating here (rather than in a separate job) keeps
# `docker compose up` a genuine one-command start for a reviewer.
set -e

if [ "${WAIT_FOR_DB:-true}" = "true" ]; then
  echo "waiting for the database..."
  python - <<'PY'
import os
import socket
import sys
import time
from urllib.parse import urlparse

url = urlparse(os.environ.get("DATABASE_URL", ""))
if url.scheme.startswith("postgres"):
    host, port = url.hostname or "localhost", url.port or 5432
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                print(f"database reachable at {host}:{port}")
                sys.exit(0)
        except OSError:
            time.sleep(1)
    sys.exit(f"database at {host}:{port} did not become reachable in 60s")
PY
fi

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "applying migrations..."
  python manage.py migrate --noinput
fi

if [ "${SEED_DATA:-false}" = "true" ]; then
  echo "seeding catalogue..."
  python manage.py seed_catalog
fi

exec "$@"
