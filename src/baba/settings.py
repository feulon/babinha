"""Django settings. Every deploy-specific value comes from environment variables."""

import os
from datetime import time
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

from baba.env import env_bool, env_int, env_list, env_str
from baba.production_checks import assert_production_environment

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Set by compose.prod.yaml: refuse to start unless the environment is safe for production.
BABA_PRODUCTION = env_bool("BABA_PRODUCTION", default=False)
if BABA_PRODUCTION:
    assert_production_environment(os.environ)

DEBUG = env_bool("DJANGO_DEBUG", default=False)

SECRET_KEY = env_str("DJANGO_SECRET_KEY", default="insecure-dev-only-key" if DEBUG else None)

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", default="localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "accounts",
    "games",
    "fines",
    "finance",
    "matchday",
    "community",
    "board",
    "demo",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "core.middleware.PrivatePagesMiddleware",
    "accounts.middleware.PhoneRequiredMiddleware",
    "accounts.middleware.LoginRedirectMiddleware",
    "board.middleware.SundayUnavailableMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.ContentSecurityPolicyMiddleware",
]

ROOT_URLCONF = "baba.urls"
WSGI_APPLICATION = "baba.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "src" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.fake_clock_banner",
                "core.context_processors.demo_banner",
                "core.context_processors.csrf_cookie",
                "core.context_processors.back_link",
                "core.context_processors.monthly_member",
                "community.context_processors.gold_ball",
                "board.context_processors.board_entry",
            ],
        },
    },
]

DATABASES = {
    "default": dj_database_url.parse(
        env_str("DATABASE_URL", default="postgres://baba:baba@localhost:5432/baba"),
        conn_max_age=env_int("DATABASE_CONN_MAX_AGE", 60),
        conn_health_checks=True,
    )
}
if DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
    raise ImproperlyConfigured("Babinha requires PostgreSQL (DATABASE_URL=postgres://...)")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.Player"
CSRF_FAILURE_VIEW = "core.views.csrf_failure"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "games:home"
LOGOUT_REDIRECT_URL = "accounts:login"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "accounts.validators.FourCharacterPasswordValidator"},
]
# Password reset links are single use (the token is bound to the password hash) and expire.
PASSWORD_RESET_TIMEOUT = env_int("BABA_PASSWORD_RESET_TIMEOUT_SECONDS", 60 * 60)

# Demo mode (see core.demo): every cookie gets its own name, so the demo on localhost never
# reads or overwrites the cookies of another app (or of the regular dev stack) on that host.
BABA_DEMO = env_bool("BABA_DEMO", default=False)
_COOKIE_PREFIX = "baba_demo_" if BABA_DEMO else ""
SESSION_COOKIE_NAME = f"{_COOKIE_PREFIX}sessionid"
CSRF_COOKIE_NAME = f"{_COOKIE_PREFIX}csrftoken"

SESSION_COOKIE_AGE = 60 * 60 * 24 * 90
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SECURE_COOKIES = env_bool("DJANGO_SECURE_COOKIES", default=not DEBUG)
SESSION_COOKIE_SECURE = SECURE_COOKIES
CSRF_COOKIE_SECURE = SECURE_COOKIES
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
# Enable only once the site is served exclusively over HTTPS (e.g. 31536000).
SECURE_HSTS_SECONDS = env_int("DJANGO_HSTS_SECONDS", 0)
if env_bool("DJANGO_BEHIND_HTTPS_PROXY", default=False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "src" / "static"]
STORAGES = {
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
    # Pix receipts live in a private bucket of any S3-compatible service (AWS S3, Cloudflare R2,
    # Backblaze B2, or the gateway in compose.yaml). They are only served through the app,
    # after checking who is asking, so the bucket never needs public access.
    "receipts": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": env_str("BABA_S3_BUCKET", default="baba-comprovantes"),
            # Empty means AWS itself; set it for any other S3-compatible service.
            "endpoint_url": env_str("BABA_S3_ENDPOINT_URL", default="") or None,
            "region_name": env_str("BABA_S3_REGION", default="us-east-1"),
            # Empty falls back to boto3's own credential chain (AWS_* variables, instance roles).
            "access_key": env_str("BABA_S3_ACCESS_KEY_ID", default="") or None,
            "secret_key": env_str("BABA_S3_SECRET_ACCESS_KEY", default="") or None,
            "addressing_style": env_str("BABA_S3_ADDRESSING_STYLE", default="path"),
            "signature_version": "s3v4",
            "default_acl": None,
            "file_overwrite": False,
            "querystring_auth": True,
        },
    },
}

EMAIL_BACKEND = env_str("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env_str("EMAIL_HOST", default="localhost")
EMAIL_PORT = env_int("EMAIL_PORT", 1025)
EMAIL_HOST_USER = env_str("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env_str("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", default=False)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", default=False)
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = env_str("DEFAULT_FROM_EMAIL", default="Babinha <baba@localhost>")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env_str("LOG_LEVEL", default="INFO")},
}


def _parse_time(name: str, default: str) -> time:
    raw = env_str(name, default=default)
    try:
        return time.fromisoformat(raw)
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} must be HH:MM, got {raw!r}") from exc


# Hour on Monday when the weekly list opens for mensalistas and avulsos.
BABA_LIST_OPENS_AT = _parse_time("BABA_LIST_OPENS_AT", "08:00")

# Shared secret of the group: sign-up asks for it when set. Required in production
# (baba.production_checks); empty keeps sign-up open, as in development.
BABA_INVITE_CODE = env_str("BABA_INVITE_CODE", default="").strip()

# Request header holding the real client IP when running behind a trusted proxy or tunnel
# (e.g. "HTTP_CF_CONNECTING_IP" for Cloudflare Tunnel). Empty means use REMOTE_ADDR.
BABA_CLIENT_IP_HEADER = env_str("BABA_CLIENT_IP_HEADER", default="")

# E-mail the "Sobre o app" screen offers for talking to the creator. Empty hides the contact.
BABA_CREATOR_EMAIL = env_str("BABA_CREATOR_EMAIL", default="").strip()

# Testing only: start the app's clock at this Sao Paulo moment (e.g. 2026-10-15T20:00) so the
# weekly list can be tried on any day. Honored only with DJANGO_DEBUG on (see
# core.fake_clock); every page then shows a banner.
BABA_FAKE_NOW = env_str("BABA_FAKE_NOW", default="")
