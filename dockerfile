# Anti-Cheat Exam App — production image (gunicorn + whitenoise)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_DEBUG=False

WORKDIR /app

# System packages needed to build mysqlclient (must come BEFORE pip install)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        pkg-config \
        default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-mysql.txt ./
RUN pip install --no-cache-dir -r requirements-mysql.txt

COPY . .

# Collect static files at build time so whitenoise's manifest storage has
# everything it needs (DEBUG=False-safe settings, see exam_system/settings.py).
RUN python manage.py collectstatic --noinput

# Bring your own persistent volume for db.sqlite3 when not using MySQL, so the
# database survives container rebuilds.
VOLUME ["/app/data"]

EXPOSE 8090

# Entrypoint: wait for DB, run migrations, create superuser, then start app.
# sed strips Windows CRLF line endings so the script runs on Linux.
RUN sed -i 's/\r$//' /app/docker-entrypoint.sh /app/start.sh /app/build.sh && \
    chmod +x /app/docker-entrypoint.sh

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["sh", "-c", "exec gunicorn exam_system.wsgi:application --bind 0.0.0.0:${PORT:-8090} --workers 3"]
