#!/bin/sh
set -e

# Ensure the SQLite file's directory exists, wherever DJANGO_DB_PATH points
# (defaults to /app/data/db.sqlite3, matching the Dockerfile's VOLUME).
DB_PATH="${DJANGO_DB_PATH:-/app/data/db.sqlite3}"
mkdir -p "$(dirname "$DB_PATH")"

echo "Running migrations..."
python manage.py migrate --noinput

exec "$@"
