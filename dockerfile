# Anti-Cheat Exam App — production image (gunicorn + whitenoise)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_DEBUG=False

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Collect static files at build time so whitenoise's manifest storage has
# everything it needs (this needs DEBUG=False-safe settings, which is the
# default in this image — see exam_system/settings.py).
RUN python manage.py collectstatic --noinput

# Bring your own persistent volume for db.sqlite3 in production (see
# docker-compose.yml) so the database survives container rebuilds.
VOLUME ["/app/data"]

EXPOSE 8000

COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["gunicorn", "exam_system.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
