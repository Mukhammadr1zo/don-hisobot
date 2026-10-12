import os
from datetime import timedelta
from pathlib import Path

from django.contrib.messages import constants as message_levels
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _flag(name, default):
    return os.environ.get(name, default) == "1"


def _csv(name):
    return [v.strip() for v in os.environ.get(name, "").split(",") if v.strip()]


DEBUG = _flag("DEBUG", "1")
SECRET_KEY = os.environ.get("SECRET_KEY") or ("dev-only-insecure-key" if DEBUG else "")
if not SECRET_KEY:
    raise ImproperlyConfigured("SECRET_KEY is required when DEBUG is off.")
ALLOWED_HOSTS = _csv("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = _csv("CSRF_TRUSTED_ORIGINS")
TRUST_PROXY = _flag("TRUST_PROXY", "0")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "hisobot",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("DB_PORT", "5440"),
        "NAME": os.environ.get("DB_NAME", "hisobot"),
        "USER": os.environ.get("DB_USER", "hisobot"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "hisobot"),
        "TEST": {"NAME": os.environ.get("TEST_DB_NAME", "test_hisobot")},
    },
}

AUTH_USER_MODEL = "hisobot.User"
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "hisobot.access.PinHasher",  # station PINs only
]

USE_I18N = False
TIME_ZONE = "Asia/Tashkent"
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Dev and tests have no collected STATIC_ROOT; without this whitenoise warns about the missing directory.
WHITENOISE_AUTOREFRESH = DEBUG

# base.html prints each message as <div class="notice-{tag}">; these are the notice classes of app.css.
MESSAGE_TAGS = {
    message_levels.DEBUG: "info",
    message_levels.INFO: "info",
    message_levels.SUCCESS: "ok",
    message_levels.WARNING: "warn",
    message_levels.ERROR: "error",
}

SESSION_COOKIE_AGE = 60 * 60 * 24 * 30
SESSION_SAVE_EVERY_REQUEST = True
CSRF_COOKIE_HTTPONLY = True

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Gunicorn configures no root logger and Django's own console handler works only with DEBUG on:
# without this a 500 in production leaves no traceback in `docker compose logs web`.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"stderr": {"class": "logging.StreamHandler"}},
    "loggers": {"django": {"handlers": ["stderr"], "level": "ERROR"}},
}

PIN_LOCK_EVERY = 5
PIN_LOCK_STEPS = [timedelta(minutes=15), timedelta(hours=1), timedelta(hours=4), timedelta(hours=24)]
PIN_IP_FAILS_PER_HOUR = 30
STAFF_IP_FAILS = 10
STAFF_IP_WINDOW = timedelta(minutes=15)
STAFF_SESSION_HOURS = 12
