import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

from baba.production_checks import assert_production_environment, production_problems

ROOT = Path(__file__).resolve().parents[2]

COMMON = {
    "DJANGO_SECRET_KEY": "k" * 20 + "Zq9-" * 10,
    "DATABASE_URL": "postgres://baba:d41d8cd98f00b204e980@db:5432/baba",
    "BABA_S3_ENDPOINT_URL": "http://s3:7070",
    "BABA_S3_BUCKET": "baba-comprovantes",
    "BABA_S3_ACCESS_KEY_ID": "a1b2c3d4e5f60718",
    "BABA_S3_SECRET_ACCESS_KEY": "s3cr3t" * 8,
    "BABA_INVITE_CODE": "7f3a9c21",
}
# v0: plain HTTP on the home network.
LOCAL = {
    **COMMON,
    "DJANGO_ALLOWED_HOSTS": "192.0.2.10,localhost",
    "DJANGO_BEHIND_HTTPS_PROXY": "false",
    "DJANGO_SECURE_COOKIES": "false",
    "DJANGO_HSTS_SECONDS": "0",
}
# Stage two: behind the Cloudflare Tunnel, which terminates HTTPS.
VALID = {
    **COMMON,
    "BABA_S3_ENDPOINT_URL": "https://abc123.r2.cloudflarestorage.com",
    "DJANGO_ALLOWED_HOSTS": "babinha.meudominio.com.br,localhost",
    "DJANGO_CSRF_TRUSTED_ORIGINS": "https://babinha.meudominio.com.br",
    "DJANGO_BEHIND_HTTPS_PROXY": "true",
    "DJANGO_SECURE_COOKIES": "true",
    "DJANGO_HSTS_SECONDS": "3600",
    "BABA_CLIENT_IP_HEADER": "HTTP_CF_CONNECTING_IP",
}


def problems_with(**overrides: str | None) -> list[str]:
    environ = {**VALID, **overrides}
    return production_problems({k: v for k, v in environ.items() if v is not None})


def test_a_complete_environment_has_no_problems() -> None:
    assert production_problems(VALID) == []
    assert_production_environment(VALID)


def local_problems(**overrides: str | None) -> list[str]:
    environ = {**LOCAL, **overrides}
    return production_problems({k: v for k, v in environ.items() if v is not None})


def test_the_local_setup_over_plain_http_is_accepted() -> None:
    assert production_problems(LOCAL) == []
    assert local_problems(DJANGO_HSTS_SECONDS=None, DJANGO_CSRF_TRUSTED_ORIGINS=None) == []
    assert local_problems(DJANGO_CSRF_TRUSTED_ORIGINS="http://192.0.2.10:8000") == []


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"DJANGO_SECURE_COOKIES": "true"}, "DJANGO_SECURE_COOKIES must be false"),
        ({"DJANGO_SECURE_COOKIES": None}, "DJANGO_SECURE_COOKIES must be false"),
        ({"BABA_CLIENT_IP_HEADER": "HTTP_X_FORWARDED_FOR"}, "BABA_CLIENT_IP_HEADER must be empty"),
        ({"DJANGO_HSTS_SECONDS": "3600"}, "DJANGO_HSTS_SECONDS must be 0"),
        ({"DJANGO_ALLOWED_HOSTS": "localhost"}, "address players type"),
        ({"DJANGO_DEBUG": "true"}, "DJANGO_DEBUG"),
        ({"BABA_FAKE_NOW": "2026-10-15T20:00"}, "BABA_FAKE_NOW"),
        ({"BABA_DEMO": "true"}, "BABA_DEMO"),
        ({"BABA_S3_ACCESS_KEY_ID": None}, "BABA_S3_ACCESS_KEY_ID"),
    ],
)
def test_the_local_setup_still_refuses_unsafe_values(
    overrides: dict[str, str | None], fragment: str
) -> None:
    problems = local_problems(**overrides)
    assert any(fragment in problem for problem in problems), problems


def test_optional_email_and_gradual_hsts_are_accepted() -> None:
    assert problems_with(DJANGO_HSTS_SECONDS=None, EMAIL_HOST=None) == []
    assert problems_with(EMAIL_HOST="smtp-relay.brevo.com", EMAIL_USE_TLS="true") == []


@pytest.mark.parametrize(
    "name",
    [
        "DJANGO_SECRET_KEY",
        "DJANGO_ALLOWED_HOSTS",
        "DJANGO_CSRF_TRUSTED_ORIGINS",
        "BABA_CLIENT_IP_HEADER",
        "DATABASE_URL",
        "BABA_S3_ENDPOINT_URL",
        "BABA_S3_BUCKET",
        "BABA_S3_ACCESS_KEY_ID",
        "BABA_S3_SECRET_ACCESS_KEY",
        "BABA_INVITE_CODE",
    ],
)
def test_each_required_variable_is_reported_when_missing(name: str) -> None:
    problems = problems_with(**{name: None})
    assert problems
    assert name.replace("BABA_S3_", "") in " ".join(problems) or name in " ".join(problems)


def test_blank_values_count_as_missing() -> None:
    assert problems_with(DJANGO_SECRET_KEY="   ")


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"DJANGO_DEBUG": "true"}, "DJANGO_DEBUG"),
        ({"BABA_FAKE_NOW": "2026-10-15T20:00"}, "BABA_FAKE_NOW"),
        ({"BABA_DEMO": "true"}, "BABA_DEMO"),
        ({"DJANGO_SECRET_KEY": "short"}, "DJANGO_SECRET_KEY"),
        ({"DJANGO_SECRET_KEY": "change-me-" + "x" * 60}, "DJANGO_SECRET_KEY"),
        ({"DJANGO_ALLOWED_HOSTS": "localhost,127.0.0.1"}, "address players type"),
        ({"DJANGO_ALLOWED_HOSTS": "babinha.meudominio.com.br"}, "keep localhost"),
        ({"DJANGO_ALLOWED_HOSTS": "*,localhost"}, "'*'"),
        ({"DJANGO_CSRF_TRUSTED_ORIGINS": "http://babinha.meudominio.com.br"}, "https://"),
        ({"DJANGO_SECURE_COOKIES": "false"}, "DJANGO_SECURE_COOKIES"),
        ({"DJANGO_HSTS_SECONDS": "um ano"}, "DJANGO_HSTS_SECONDS"),
        ({"DATABASE_URL": "postgres://baba:baba@db:5432/baba"}, "development password"),
        ({"BABA_S3_SECRET_ACCESS_KEY": "baba-local-secret"}, "example value"),
        ({"EMAIL_BACKEND": "django.core.mail.backends.console.EmailBackend"}, "EMAIL_BACKEND"),
        ({"EMAIL_HOST": "mailpit"}, "Mailpit"),
        ({"BABA_INVITE_CODE": "abc"}, "BABA_INVITE_CODE must have at least"),
        ({"BABA_INVITE_CODE": "change-me"}, "BABA_INVITE_CODE is still an example"),
        ({"BABA_INVITE_CODE": "   "}, "BABA_INVITE_CODE is required"),
    ],
)
def test_unsafe_values_are_rejected(overrides: dict[str, str], fragment: str) -> None:
    problems = problems_with(**overrides)
    assert any(fragment in problem for problem in problems), problems


def test_every_problem_is_listed_in_one_error() -> None:
    with pytest.raises(ImproperlyConfigured) as error:
        assert_production_environment({"BABA_FAKE_NOW": "2026-10-15T20:00"})
    message = str(error.value)
    assert "BABA_FAKE_NOW" in message
    assert "DJANGO_SECRET_KEY" in message
    assert "DATABASE_URL" in message


def test_a_malformed_boolean_is_a_problem_not_a_crash() -> None:
    assert any("DJANGO_DEBUG" in p for p in problems_with(DJANGO_DEBUG="talvez"))


def run_settings(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Import the real settings module in a clean process, as gunicorn would."""
    base = {k: v for k, v in os.environ.items() if k.startswith(("PATH", "HOME", "LANG"))}
    return subprocess.run(
        [sys.executable, "-c", "import baba.settings"],
        cwd=ROOT / "src",
        env={**base, **env},
        capture_output=True,
        text=True,
        check=False,
    )


def test_settings_refuse_to_start_in_production_with_the_fake_clock_set() -> None:
    result = run_settings({**VALID, "BABA_PRODUCTION": "true", "BABA_FAKE_NOW": "2026-10-15T20:00"})
    assert result.returncode != 0
    assert "BABA_FAKE_NOW must be empty in production" in result.stderr


def test_settings_refuse_to_start_in_production_with_the_demo_on() -> None:
    result = run_settings({**VALID, "BABA_PRODUCTION": "true", "BABA_DEMO": "true"})
    assert result.returncode != 0
    assert "BABA_DEMO must be false in production" in result.stderr


def test_the_demo_names_its_own_cookies_so_it_never_clashes_on_localhost() -> None:
    script = "import baba.settings as s;print(s.SESSION_COOKIE_NAME, s.CSRF_COOKIE_NAME)"

    def cookies(env: dict[str, str]) -> list[str]:
        base = {k: v for k, v in os.environ.items() if k.startswith(("PATH", "HOME", "LANG"))}
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT / "src",
            env={**base, "DJANGO_SECRET_KEY": "dev", **env},
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.split()

    assert cookies({}) == ["sessionid", "csrftoken"]
    assert cookies({"BABA_DEMO": "true"}) == [
        "baba_demo_sessionid",
        "baba_demo_csrftoken",
    ]


def test_settings_start_in_production_with_a_valid_environment() -> None:
    result = run_settings({**VALID, "BABA_PRODUCTION": "true"})
    assert result.returncode == 0, result.stderr


def test_settings_skip_the_checks_outside_production() -> None:
    result = run_settings({"DJANGO_SECRET_KEY": "dev", "BABA_FAKE_NOW": "2026-10-15T20:00"})
    assert result.returncode == 0, result.stderr


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            name, _, value = line.partition("=")
            values[name] = value.strip('"')
    return values


def example_values() -> dict[str, str]:
    return parse_env((ROOT / ".env.production.example").read_text())


# What compose.prod.yaml adds to the app container on top of .env.production.
COMPOSE_LOCAL_DEFAULTS = {
    "DJANGO_BEHIND_HTTPS_PROXY": "false",
    "DJANGO_SECURE_COOKIES": "false",
    "DJANGO_HSTS_SECONDS": "0",
    "BABA_S3_ENDPOINT_URL": "http://s3:7070",
    "DATABASE_URL": COMMON["DATABASE_URL"],
}


def test_the_example_file_never_holds_a_secret_and_never_enables_testing_switches() -> None:
    example = example_values()
    for name in (
        "TUNNEL_TOKEN",
        "DJANGO_SECRET_KEY",
        "POSTGRES_PASSWORD",
        "BABA_S3_ACCESS_KEY_ID",
        "BABA_S3_SECRET_ACCESS_KEY",
        "EMAIL_HOST_PASSWORD",
    ):
        assert example.get(name, "") == ""
    assert example["DJANGO_DEBUG"] == "false"
    assert "BABA_FAKE_NOW" not in example
    assert "BABA_DEMO" not in example


def test_the_example_file_is_refused_until_the_secrets_are_filled_in() -> None:
    problems = production_problems({**COMPOSE_LOCAL_DEFAULTS, **example_values()})
    assert any("DJANGO_SECRET_KEY" in p for p in problems)
    assert any("BABA_S3_ACCESS_KEY_ID" in p for p in problems)
    assert any("BABA_INVITE_CODE" in p for p in problems)


def test_the_example_file_with_the_secrets_filled_in_is_accepted() -> None:
    filled = {
        **COMPOSE_LOCAL_DEFAULTS,
        **example_values(),
        "DJANGO_SECRET_KEY": COMMON["DJANGO_SECRET_KEY"],
        "BABA_S3_ACCESS_KEY_ID": COMMON["BABA_S3_ACCESS_KEY_ID"],
        "BABA_S3_SECRET_ACCESS_KEY": COMMON["BABA_S3_SECRET_ACCESS_KEY"],
        "BABA_INVITE_CODE": COMMON["BABA_INVITE_CODE"],
    }
    assert production_problems(filled) == []


def compose_lines(name: str) -> str:
    lines = (ROOT / name).read_text().splitlines()
    return "\n".join(line.strip() for line in lines if not line.strip().startswith("#"))


def test_the_production_compose_has_no_dev_services_and_only_the_app_is_published() -> None:
    text = compose_lines("compose.prod.yaml")
    assert text.count("ports:") == 1  # the app, on the home network
    assert "mailpit" not in text
    assert "restart: unless-stopped" in text
    assert text.count("healthcheck:") == 3
    assert "cloudflared" not in text


def test_the_tunnel_compose_takes_the_port_off_the_network() -> None:
    text = compose_lines("compose.prod.tunnel.yaml")
    assert "ports: !reset []" in text
    assert "TUNNEL_TOKEN: ${TUNNEL_TOKEN:?" in text
    assert "restart: unless-stopped" in text
    assert "healthcheck:" in text
