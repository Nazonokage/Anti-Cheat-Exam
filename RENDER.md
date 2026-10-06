# Deploy to Render with Neon

The repository includes a native Python Render Blueprint in `render.yaml`.
The database stays on Neon. Credentials belong in Render's environment
settings; the local `.env` file is ignored by Git and is not uploaded.

1. Push this repository to your Git provider.
2. In Render, choose **New → Blueprint** and connect the repository.
3. When prompted for `DATABASE_URL`, paste the full Neon connection string
   from your local `.env`, including `sslmode=require&channel_binding=require`.
4. Deploy. Render installs dependencies and collects static files with
   `bash build.sh`. `bash start.sh` applies migrations, then starts Gunicorn
   on Render's assigned port. Migration failure stops startup.
5. Create an administrator once with `python manage.py createsuperuser` in
   the Render shell, if available on your plan, or from a local environment
   with dependencies installed and `.env` pointing at the same Neon database.
6. Open the service URL and check login, admin styling, and `/healthz/`.

The Blueprint uses the free plan for initial setup. Free services can sleep
when idle; select a suitable always-on plan before running scheduled exams.
The service region is Singapore, matching the supplied Neon's region.
See [Render's Django guide](https://render.com/docs/deploy-django) and
[free-service limitations](https://render.com/docs/free).

## Settings

- Python 3.12 is selected by `.python-version`.
- Render generates `DJANGO_SECRET_KEY`. Debugging is disabled, HTTPS is
  enforced, and session/CSRF cookies are secure.
- The hostname from `RENDER_EXTERNAL_HOSTNAME` is automatically allowed
  and added as a trusted HTTPS origin.
- For a custom domain, set `DJANGO_ALLOWED_HOSTS` to both the Render hostname
  and your custom hostname, separated by commas, and set
  `DJANGO_CSRF_TRUSTED_ORIGINS` to the custom domain's full HTTPS origin.
- `WEB_CONCURRENCY` optionally changes the default of two Gunicorn workers.
- Static files use WhiteNoise's compressed manifest storage. `/healthz/`
  checks process liveness without waking Neon for every probe.

Migrations run at startup so the setup also works without a paid pre-deploy
step. Keep one instance with this setup. Before scaling to multiple instances,
move `python manage.py migrate --noinput` to a single Render pre-deploy step
and remove it from `start.sh`.

## Existing data

Migrations create database tables; they do not copy existing exams, users,
or submissions from SQLite, MySQL, or Railway. Back up and transfer existing
data separately before directing students to the new deployment. This setup
does not modify the old database or import `anticheat_exam.sql`.

## Local development

To run Docker locally against the same Neon database:

```bash
docker compose up --build -d
```

Open http://localhost:8091. This configuration reads `.env`, runs migrations,
and starts only the web service. It does not start the legacy MySQL service or
create an administrator automatically. It binds only to the local computer.
The default `docker-compose.yml` includes the Neon configuration and uses the
`anti-cheat-neon` project name. Use `docker compose exec web python manage.py createsuperuser`
to create another administrator interactively. Stop with `docker compose down`;
data remains in Neon.

Install `requirements.txt` and use the Neon `.env` connection. To continue
using local MySQL instead, install `requirements-mysql.txt`, remove
`DATABASE_URL`, and use the commented `DB_*` example settings.
