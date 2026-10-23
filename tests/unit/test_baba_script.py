import os
import stat
import subprocess
from pathlib import Path

from baba.production_checks import production_problems
from tests.unit.test_production_checks import COMPOSE_LOCAL_DEFAULTS, ROOT, parse_env

SCRIPT = ROOT / "scripts" / "baba.sh"


def run(env_file: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *args],
        env={**os.environ, "BABA_ENV_FILE": str(env_file)},
        capture_output=True,
        text=True,
        check=False,
    )


def test_init_creates_a_private_env_file_the_app_accepts(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.production"
    result = run(env_file, "init")
    assert result.returncode == 0, result.stderr

    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    values = parse_env(env_file.read_text())
    assert len(values["DJANGO_SECRET_KEY"]) >= 50
    assert len(values["POSTGRES_PASSWORD"]) == 48
    host = values["DJANGO_ALLOWED_HOSTS"].split(",")[0]
    assert host not in ("", "localhost")
    assert values["DJANGO_ALLOWED_HOSTS"].endswith(",localhost")
    assert len(values["BABA_INVITE_CODE"]) == 8
    assert values["BABA_INVITE_CODE"] in result.stderr  # the president is told the code to share
    # With what compose adds for the local setup, the app's own startup checks pass.
    assert production_problems({**COMPOSE_LOCAL_DEFAULTS, **values}) == []


def test_init_generates_different_secrets_each_time(tmp_path: Path) -> None:
    first, second = tmp_path / "a.env", tmp_path / "b.env"
    run(first, "init")
    run(second, "init")
    assert (
        parse_env(first.read_text())["DJANGO_SECRET_KEY"]
        != (parse_env(second.read_text())["DJANGO_SECRET_KEY"])
    )


def test_init_never_overwrites_an_existing_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.production"
    env_file.write_text("DJANGO_SECRET_KEY=keep-me\n")
    assert run(env_file, "init").returncode == 0
    assert env_file.read_text() == "DJANGO_SECRET_KEY=keep-me\n"


def test_without_a_known_command_it_prints_the_usage(tmp_path: Path) -> None:
    assert "subir" in run(tmp_path / "x.env").stdout
    result = run(tmp_path / "x.env", "bogus")
    assert result.returncode != 0


def fake_docker(tmp_path: Path) -> dict[str, str]:
    """A `docker` on PATH that only records its arguments, so no stack is needed."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text('#!/bin/sh\necho "docker $*"\n')
    docker.chmod(0o755)
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}


def test_apagar_jogador_runs_the_command_in_the_app_and_shows_the_last_backup(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env.production"
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "baba-db-20261001T030000Z.sql.gz").write_text("old")
    newest = backups / "baba-db-20261020T030000Z.sql.gz"
    newest.write_text("new")
    os.utime(backups / "baba-db-20261001T030000Z.sql.gz", (1, 1))
    env_file.write_text(f"BABA_BACKUP_DIR={backups}\n")
    result = subprocess.run(
        [str(SCRIPT), "apagar-jogador", "11988887777", "--confirmar"],
        env={**os.environ, **fake_docker(tmp_path), "BABA_ENV_FILE": str(env_file)},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    # No terminal here: -T, so the command sees no TTY and asks for --sim-tenho-certeza.
    assert "exec -T app python manage.py apagar_jogador 11988887777 --confirmar" in result.stdout
    assert "Ultimo backup: " in result.stderr
    assert str(newest) in result.stderr


def test_apagar_jogador_needs_a_phone(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.production"
    env_file.write_text("")
    result = subprocess.run(
        [str(SCRIPT), "apagar-jogador"],
        env={**os.environ, **fake_docker(tmp_path), "BABA_ENV_FILE": str(env_file)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "docker" not in result.stdout
