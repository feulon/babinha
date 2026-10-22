"""Settings for the test suite: production settings with safe local defaults."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-secret-key")
os.environ.setdefault("DJANGO_SECURE_COOKIES", "false")

from baba.settings import *  # noqa: F403

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
STORAGES = {
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    # Receipts stay in memory; tests/integration/test_s3_storage.py covers a real S3 server.
    "receipts": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
}

# Production uses Argon2 (see tests/integration/test_auth.py); tests hash fast.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Serve static files straight from the finders, no collectstatic needed.
WHITENOISE_AUTOREFRESH = True
WHITENOISE_USE_FINDERS = True
