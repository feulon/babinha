"""Startup validation for the production deploy (BABA_PRODUCTION=true).

Two setups are valid. The local one (v0) serves plain HTTP on the home network:
DJANGO_BEHIND_HTTPS_PROXY=false, cookies not Secure, no proxy header. The public one sits behind
a Cloudflare Tunnel that terminates HTTPS: DJANGO_BEHIND_HTTPS_PROXY=true with Secure cookies, the
client IP header and https origins. Anything in between (Secure cookies over plain HTTP would
make logins impossible; a proxy header with no proxy lets visitors forge their IP) is refused.

Settings call `assert_production_environment` before anything else is built, so a missing or
unsafe variable stops the app (and `manage.py`) with one message listing every problem, instead
of the app running half configured. Outside production nothing here runs.
"""

from collections.abc import Mapping

from django.core.exceptions import ImproperlyConfigured

from baba.env import parse_bool

MIN_SECRET_KEY_LENGTH = 50
MIN_INVITE_CODE_LENGTH = 6
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}
# Defaults and examples that must never reach production.
PLACEHOLDER_SECRETS = {"change-me", "insecure-dev-only-key", "local-compose-secret-change-me"}
PLACEHOLDER_MARKERS = ("change-me", "troque", "example", "insecure")
S3_REQUIRED = (
    "BABA_S3_ENDPOINT_URL",
    "BABA_S3_BUCKET",
    "BABA_S3_ACCESS_KEY_ID",
    "BABA_S3_SECRET_ACCESS_KEY",
)


def _get(environ: Mapping[str, str], name: str) -> str:
    return environ.get(name, "").strip()


def _flag(environ: Mapping[str, str], name: str, problems: list[str]) -> bool | None:
    """The boolean value of `name`, or None (and a problem) when it is not a boolean."""
    try:
        return parse_bool(name, environ.get(name), default=False)
    except ImproperlyConfigured as exc:
        problems.append(str(exc))
        return None


def _items(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _is_placeholder(value: str) -> bool:
    lowered = value.lower()
    return lowered in PLACEHOLDER_SECRETS or any(
        marker in lowered for marker in PLACEHOLDER_MARKERS
    )


def _check_secret(environ: Mapping[str, str], problems: list[str]) -> None:
    secret = _get(environ, "DJANGO_SECRET_KEY")
    if not secret:
        problems.append("DJANGO_SECRET_KEY is required")
    elif len(secret) < MIN_SECRET_KEY_LENGTH or _is_placeholder(secret):
        problems.append(
            f"DJANGO_SECRET_KEY must be a random string of at least {MIN_SECRET_KEY_LENGTH} "
            "characters, not the example value"
        )


def _check_hosts(environ: Mapping[str, str], problems: list[str]) -> None:
    hosts = _items(_get(environ, "DJANGO_ALLOWED_HOSTS"))
    public = [host for host in hosts if host not in LOCAL_HOSTS and not host.startswith("*")]
    if not public:
        problems.append(
            "DJANGO_ALLOWED_HOSTS must list the address players type "
            "(the notebook's IP on the home network, or the public domain)"
        )
    if "*" in hosts:
        problems.append("DJANGO_ALLOWED_HOSTS must not contain '*'")
    if "localhost" not in hosts:
        problems.append(
            "DJANGO_ALLOWED_HOSTS must keep localhost: the container healthcheck calls "
            "http://localhost:8000/saude/"
        )


def _check_origins(environ: Mapping[str, str], behind_https: bool, problems: list[str]) -> None:
    origins = _items(_get(environ, "DJANGO_CSRF_TRUSTED_ORIGINS"))
    if behind_https and not origins:
        problems.append(
            "DJANGO_CSRF_TRUSTED_ORIGINS is required (e.g. https://babinha.example.com)"
        )
    if behind_https and not all(origin.startswith("https://") for origin in origins):
        problems.append("DJANGO_CSRF_TRUSTED_ORIGINS must only hold https:// origins")


def _check_transport(environ: Mapping[str, str], problems: list[str]) -> None:
    behind_https = bool(_flag(environ, "DJANGO_BEHIND_HTTPS_PROXY", problems))
    secure = (
        _flag(environ, "DJANGO_SECURE_COOKIES", problems)
        if "DJANGO_SECURE_COOKIES" in environ
        else None
    )
    hsts = _get(environ, "DJANGO_HSTS_SECONDS")
    if hsts and not hsts.isdigit():
        problems.append(f"DJANGO_HSTS_SECONDS must be a number of seconds, got {hsts!r}")
    header = _get(environ, "BABA_CLIENT_IP_HEADER")
    _check_origins(environ, behind_https, problems)
    if behind_https:
        if secure is False:
            problems.append("DJANGO_SECURE_COOKIES must be true behind the HTTPS tunnel")
        if not header:
            problems.append(
                "BABA_CLIENT_IP_HEADER is required behind the tunnel (HTTP_CF_CONNECTING_IP for "
                "Cloudflare), otherwise every player shares one login-attempt limit"
            )
        return
    # Local setup: plain HTTP on the home network, no proxy in front of the app.
    if secure is not False:
        problems.append(
            "DJANGO_SECURE_COOKIES must be false without HTTPS: browsers drop Secure cookies "
            "over plain HTTP and nobody could log in"
        )
    if header:
        problems.append("BABA_CLIENT_IP_HEADER must be empty without a proxy: it could be forged")
    if hsts not in ("", "0"):
        problems.append("DJANGO_HSTS_SECONDS must be 0 without HTTPS")


def _check_database(environ: Mapping[str, str], problems: list[str]) -> None:
    url = _get(environ, "DATABASE_URL")
    if not url:
        problems.append("DATABASE_URL is required")
    elif url.startswith("postgres://baba:baba@") or _is_placeholder(url):
        problems.append("DATABASE_URL still uses the local development password")


def _check_receipts(environ: Mapping[str, str], problems: list[str]) -> None:
    missing = [name for name in S3_REQUIRED if not _get(environ, name)]
    if missing:
        problems.append(f"Receipts bucket is not configured, missing: {', '.join(missing)}")
    secret = _get(environ, "BABA_S3_SECRET_ACCESS_KEY")
    if secret and (secret == "baba-local-secret" or _is_placeholder(secret)):
        problems.append("BABA_S3_SECRET_ACCESS_KEY is still an example value")


def _check_testing_switches(environ: Mapping[str, str], problems: list[str]) -> None:
    if _flag(environ, "DJANGO_DEBUG", problems):
        problems.append("DJANGO_DEBUG must be false in production")
    if _get(environ, "BABA_FAKE_NOW"):
        problems.append("BABA_FAKE_NOW must be empty in production")
    if _flag(environ, "BABA_DEMO", problems):
        problems.append("BABA_DEMO must be false in production: it releases the game day schedule")


def _check_invite_code(environ: Mapping[str, str], problems: list[str]) -> None:
    code = _get(environ, "BABA_INVITE_CODE")
    if not code:
        problems.append("BABA_INVITE_CODE is required: without it anyone with the URL can sign up")
    elif len(code) < MIN_INVITE_CODE_LENGTH:
        problems.append(f"BABA_INVITE_CODE must have at least {MIN_INVITE_CODE_LENGTH} characters")
    elif _is_placeholder(code):
        problems.append("BABA_INVITE_CODE is still an example value")


def _check_email(environ: Mapping[str, str], problems: list[str]) -> None:
    # E-mail is optional, but a half configured or test backend is not.
    backend = _get(environ, "EMAIL_BACKEND")
    if "console" in backend or "filebased" in backend:
        problems.append("EMAIL_BACKEND must not be a console or file backend: it logs reset links")
    if _get(environ, "EMAIL_HOST") == "mailpit":
        problems.append("EMAIL_HOST points at Mailpit, which only captures e-mail")


def production_problems(environ: Mapping[str, str]) -> list[str]:
    """Every reason `environ` is not safe to run in production (empty when it is)."""
    problems: list[str] = []
    for check in (
        _check_testing_switches,
        _check_secret,
        _check_hosts,
        _check_transport,
        _check_database,
        _check_receipts,
        _check_invite_code,
        _check_email,
    ):
        check(environ, problems)
    return problems


def assert_production_environment(environ: Mapping[str, str]) -> None:
    problems = production_problems(environ)
    if problems:
        details = "\n".join(f"  - {problem}" for problem in problems)
        raise ImproperlyConfigured(
            f"BABA_PRODUCTION is on but the environment is not ready for production:\n{details}"
        )
