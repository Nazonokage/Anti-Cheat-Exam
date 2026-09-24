"""
Django settings for exam_system project.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/6.0/ref/settings/
"""

import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines from a .env file without overriding existing env vars."""
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(BASE_DIR / ".env")


# SECURITY WARNING: keep the secret key used in production secret!
# Overridable via the DJANGO_SECRET_KEY env var (e.g. in Docker/production).
# The fallback below is fine for local dev on a closed LAN, same as before.
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-^mf(f2s%1%$qh4k&sm3bw3@-^6-uhggy*2@%42)lo#s(!1mgrc",
)

# SECURITY WARNING: don't run with debug turned on in production!
# Defaults to True (same as before) so local `manage.py runserver` behaves
# exactly as it always has. Set DJANGO_DEBUG=False in Docker/production.
DEBUG = os.environ.get("DJANGO_DEBUG", "True") == "True"

# Comma-separated list via DJANGO_ALLOWED_HOSTS, e.g. "myexamapp.local,10.0.0.5"
# Defaults to '*' (same as before) for easy LAN access out of the box.
_allowed_hosts = os.environ.get("DJANGO_ALLOWED_HOSTS", "*")
ALLOWED_HOSTS = [h.strip() for h in _allowed_hosts.split(",")] if _allowed_hosts else []
# Comma-separated list via DJANGO_CSRF_TRUSTED_ORIGINS
# Example:
# "https://abc.ngrok-free.app,https://mydomain.com"

_csrf_trusted_origins = os.environ.get(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    "https://*.ngrok-free.app"
)

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in _csrf_trusted_origins.split(",")
    if origin.strip()
]


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
]

ROOT_URLCONF = 'exam_system.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'exam_system.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases
#
# Resolution order:
# 1. DATABASE_URL (Railway / hosted MySQL or Postgres, e.g. mysql://user:pass@host:3306/db)
# 2. Discrete env vars for local XAMPP MySQL:
#    DB_ENGINE, DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT
# 3. SQLite fallback (DJANGO_DB_PATH or BASE_DIR/db.sqlite3)

def _engine_from_scheme(scheme: str) -> str:
    scheme = (scheme or "").split("+")[0].lower()
    mapping = {
        "postgres": "django.db.backends.postgresql",
        "postgresql": "django.db.backends.postgresql",
        "pgsql": "django.db.backends.postgresql",
        "mysql": "django.db.backends.mysql",
        "mariadb": "django.db.backends.mysql",
        "sqlite": "django.db.backends.sqlite3",
        "django.db.backends.mysql": "django.db.backends.mysql",
        "django.db.backends.postgresql": "django.db.backends.postgresql",
        "django.db.backends.sqlite3": "django.db.backends.sqlite3",
    }
    if scheme not in mapping:
        raise ValueError(f"Unsupported database engine/scheme: {scheme}")
    return mapping[scheme]


def _mysql_options():
    return {
        "charset": "utf8mb4",
        "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
    }


def _db_from_url(url: str) -> dict:
    parsed = urlparse(url)
    engine = _engine_from_scheme(parsed.scheme)
    if engine.endswith("sqlite3"):
        name = unquote(parsed.path)
        if name.startswith("/") and len(name) > 1 and not name.startswith("//"):
            name = name
        return {"ENGINE": engine, "NAME": name or ":memory:"}

    config = {
        "ENGINE": engine,
        "NAME": unquote((parsed.path or "").lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "",
        "PORT": str(parsed.port or ""),
    }
    if engine.endswith("mysql"):
        config["OPTIONS"] = _mysql_options()
    return config


def _configure_databases() -> dict:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if database_url:
        return {"default": _db_from_url(database_url)}

    db_engine = os.environ.get("DB_ENGINE", "").strip()
    db_name = os.environ.get("DB_NAME", "").strip()
    if db_engine or db_name:
        engine = _engine_from_scheme(db_engine or "mysql")
        config = {
            "ENGINE": engine,
            "NAME": db_name or "anticheat_exam",
            "USER": os.environ.get("DB_USER", "root"),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
            "PORT": os.environ.get("DB_PORT", "3306" if engine.endswith("mysql") else ""),
        }
        if engine.endswith("mysql"):
            config["OPTIONS"] = _mysql_options()
        if engine.endswith("sqlite3"):
            config = {
                "ENGINE": engine,
                "NAME": db_name or os.environ.get("DJANGO_DB_PATH", str(BASE_DIR / "db.sqlite3")),
            }
        return {"default": config}

    return {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.environ.get("DJANGO_DB_PATH", str(BASE_DIR / "db.sqlite3")),
        }
    }


DATABASES = _configure_databases()

# Tests always use in-memory SQLite so `manage.py test` cannot wipe XAMPP data.
if "test" in sys.argv:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
        }
    }

# Stock XAMPP (MariaDB 10.4) is below Django 5.2+/6's MariaDB 10.5 floor.
# Local-only: set DJANGO_RELAX_MYSQL_VERSION=1 in .env. Do not use in production.
if os.environ.get("DJANGO_RELAX_MYSQL_VERSION", "").strip() in {"1", "true", "True", "yes"}:
    from django.db.backends.base.base import BaseDatabaseWrapper
    from django.db.backends.mysql.features import DatabaseFeatures

    BaseDatabaseWrapper.check_database_version_supported = lambda self: None
    DatabaseFeatures.can_return_columns_from_insert = property(
        lambda self: self.connection.mysql_is_mariadb and self.connection.mysql_version >= (10, 5, 0)
    )


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']

STATIC_ROOT = BASE_DIR / "staticfiles"

STATICFILES_STORAGE = (
    "whitenoise.storage.CompressedManifestStaticFilesStorage"
)

# Default primary key field type
# https://docs.djangoproject.com/en/6.0/ref/settings/#default-auto-field
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
