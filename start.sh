#!/usr/bin/env bash
set -euo pipefail
: "${DATABASE_URL:?Set DATABASE_URL to the Neon connection string}"
python manage.py migrate --noinput
exec gunicorn exam_system.wsgi:application --bind "0.0.0.0:${PORT:-8000}" --workers "${WEB_CONCURRENCY:-2}" --access-logfile - --error-logfile -
