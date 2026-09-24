#!/bin/sh
set -e

DB_ENGINE="${DB_ENGINE:-}"
DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-3306}"
DATABASE_URL="${DATABASE_URL:-}"

if [ -n "$DATABASE_URL" ] || [ "$DB_ENGINE" = "django.db.backends.mysql" ] || [ -n "${DB_NAME:-}" ]; then
  python - <<'PY'
import os
import socket
import time

host = os.environ.get('DB_HOST', '127.0.0.1')
port = int(os.environ.get('DB_PORT', '3306'))
for attempt in range(1, 61):
    try:
        with socket.create_connection((host, port), timeout=2):
            break
    except OSError:
        if attempt == 60:
            raise SystemExit(f"Timed out waiting for MySQL at {host}:{port}")
        time.sleep(2)
PY
fi

# Ensure the SQLite fallback path exists when the app is not using MySQL.
if [ -z "$DATABASE_URL" ] && [ "$DB_ENGINE" != "django.db.backends.mysql" ] && [ -z "${DB_NAME:-}" ]; then
  DB_PATH="${DJANGO_DB_PATH:-/app/data/db.sqlite3}"
  mkdir -p "$(dirname "$DB_PATH")"
fi

echo "Running migrations..."
python manage.py migrate --noinput

exec "$@"