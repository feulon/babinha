"""scripts/backup.sh and scripts/restore.sh against a throwaway Postgres container.

The database is reached the way the scripts do in production (a command prefix that runs inside
the db container), here `docker exec -i <disposable container>`. rclone is a small stand-in that
treats `fake:<name>` as a folder, plus the real rclone (local folder as the remote) when it is
installed. Skipped when Docker is not available.
"""

import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKUP = ROOT / "scripts" / "backup.sh"
RESTORE = ROOT / "scripts" / "restore.sh"
POSTGRES_IMAGE = "postgres:17"

FAKE_RCLONE = """#!/usr/bin/env bash
# Minimal rclone: "copy SRC fake:NAME --include GLOB" and "delete fake:NAME --min-age Nd ...".
set -euo pipefail
cmd="$1"; target="$3"; [ "$cmd" = delete ] && target="$2"
[ "$cmd" = copy ] && target="$3"
dest="$FAKE_RCLONE_ROOT/${target#fake:}"
mkdir -p "$dest"
case "$cmd" in
    copy) cp -n "$2"/baba-* "$dest"/ ;;
    delete)
        days="${4%d}"
        find "$dest" -name 'baba-*.gpg' -mtime "+$days" -delete ;;
    *) echo "unsupported: $cmd" >&2; exit 2 ;;
esac
"""

pytestmark = pytest.mark.skipif(
    shutil.which("docker") is None
    or subprocess.run(["docker", "info"], capture_output=True, check=False).returncode != 0
    or shutil.which("gpg") is None,
    reason="needs docker and gpg",
)


def sh(*args: str, env: dict[str, str] | None = None, stdin: str | None = None):
    return subprocess.run(
        list(args), capture_output=True, text=True, input=stdin, env=env, check=False
    )


@pytest.fixture(scope="module")
def postgres() -> Iterator[str]:
    name = f"baba-backup-test-{uuid.uuid4().hex[:8]}"
    started = sh(
        "docker", "run", "-d", "--rm", "--name", name, "-e", "POSTGRES_USER=baba",
        "-e", "POSTGRES_PASSWORD=baba", "-e", "POSTGRES_DB=baba", POSTGRES_IMAGE,
    )  # fmt: skip
    assert started.returncode == 0, started.stderr
    try:
        for _ in range(60):
            ready = sh("docker", "exec", name, "pg_isready", "-U", "baba", "-d", "baba")
            # The image restarts the server once after initialising; wait for a real query.
            if ready.returncode == 0 and psql(name, "select 1").returncode == 0:
                break
            time.sleep(1)
        else:
            pytest.fail("postgres did not become ready")
        yield name
    finally:
        sh("docker", "rm", "-f", name)


def psql(container: str, sql: str):
    return sh("docker", "exec", "-i", container, "psql", "-U", "baba", "-d", "baba", "-tA",
              "-v", "ON_ERROR_STOP=1", "-c", sql)  # fmt: skip


@pytest.fixture
def database(postgres: str) -> str:
    """The container with a clean public schema holding two known players."""
    reset = "drop schema public cascade; create schema public;"
    seed = (
        "create table players (id serial primary key, name text not null);"
        "insert into players (name) values ('Fernando'), ('Maria da Silva');"
        "create table payments (id serial primary key, player_id int references players, "
        "amount numeric(8,2));"
        "insert into payments (player_id, amount) values (1, 100.00), (2, 30.50);"
    )
    assert psql(postgres, reset + seed).returncode == 0
    return postgres


@pytest.fixture
def passphrase(tmp_path: Path) -> Path:
    path = tmp_path / "passphrase"
    path.write_text("uma frase longa so para o teste\n")
    path.chmod(0o600)
    return path


@pytest.fixture
def remote_root(tmp_path: Path) -> Path:
    return tmp_path / "remote"


@pytest.fixture
def run_script(
    database: str, passphrase: Path, remote_root: Path, tmp_path: Path
) -> Callable[..., subprocess.CompletedProcess[str]]:
    fake = tmp_path / "bin" / "rclone"
    fake.parent.mkdir()
    fake.write_text(FAKE_RCLONE)
    fake.chmod(0o755)

    def run(script: Path, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "BABA_ENV_FILE": str(tmp_path / "missing.env"),
            "BABA_DB_EXEC": f"docker exec -i {database}",
            "BABA_RCLONE": str(fake),
            "FAKE_RCLONE_ROOT": str(remote_root),
            "BABA_BACKUP_REMOTE": "fake:bucket",
            "BABA_BACKUP_PASSPHRASE_FILE": str(passphrase),
            "BABA_BACKUP_DIR": str(tmp_path / "staging"),
            "BABA_RESTORE_YES": "1",
            **extra,
        }
        return sh(str(script), *args, env=env)

    return run


def uploaded(remote_root: Path, pattern: str) -> list[Path]:
    return sorted((remote_root / "bucket").glob(pattern))


def test_backup_uploads_an_encrypted_compressed_dump(
    run_script: Callable[..., subprocess.CompletedProcess[str]],
    remote_root: Path,
    tmp_path: Path,
    passphrase: Path,
) -> None:
    result = run_script(BACKUP)
    assert result.returncode == 0, result.stderr

    [dump] = uploaded(remote_root, "baba-db-*.sql.gz.gpg")
    raw = dump.read_bytes()
    assert b"Maria da Silva" not in raw
    assert not raw.startswith(b"\x1f\x8b")  # not a bare gzip: it is encrypted
    assert (tmp_path / "staging" / dump.name).exists()
    assert (tmp_path / "staging" / "last-success").read_text().strip().endswith("Z")
    assert not list((tmp_path / "staging").glob("*.partial"))

    plain = subprocess.run(
        f"gpg --batch -q --pinentry-mode loopback --passphrase-file {passphrase} -d {dump} "
        "| gunzip",
        shell=True,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "Maria da Silva" in plain


def test_restore_brings_back_deleted_and_changed_data(
    run_script: Callable[..., subprocess.CompletedProcess[str]],
    database: str,
    remote_root: Path,
) -> None:
    assert run_script(BACKUP).returncode == 0
    [dump] = uploaded(remote_root, "baba-db-*.sql.gz.gpg")

    damage = (
        "delete from payments; delete from players where id = 1;"
        "update players set name = 'CORROMPIDO';"
        "create table lixo (x int);"
    )
    assert psql(database, damage).returncode == 0

    result = run_script(RESTORE, str(dump))
    assert result.returncode == 0, result.stderr

    players = psql(database, "select id, name from players order by id").stdout.split()
    assert players == ["1|Fernando", "2|Maria", "da", "Silva"]
    assert psql(database, "select sum(amount) from payments").stdout.strip() == "130.50"


def test_restore_into_an_empty_database_for_a_new_machine(
    run_script: Callable[..., subprocess.CompletedProcess[str]],
    database: str,
    remote_root: Path,
) -> None:
    assert run_script(BACKUP).returncode == 0
    [dump] = uploaded(remote_root, "baba-db-*.sql.gz.gpg")
    assert psql(database, "drop schema public cascade; create schema public;").returncode == 0

    assert run_script(RESTORE, str(dump)).returncode == 0
    assert psql(database, "select count(*) from players").stdout.strip() == "2"


def test_restore_with_the_wrong_passphrase_changes_nothing(
    run_script: Callable[..., subprocess.CompletedProcess[str]],
    database: str,
    remote_root: Path,
    tmp_path: Path,
) -> None:
    assert run_script(BACKUP).returncode == 0
    [dump] = uploaded(remote_root, "baba-db-*.sql.gz.gpg")
    wrong = tmp_path / "wrong"
    wrong.write_text("outra frase\n")
    assert psql(database, "update players set name = 'DEPOIS'").returncode == 0

    result = run_script(RESTORE, str(dump), BABA_BACKUP_PASSPHRASE_FILE=str(wrong))
    assert result.returncode != 0
    assert psql(database, "select distinct name from players").stdout.strip() == "DEPOIS"


def test_restore_asks_for_confirmation(
    run_script: Callable[..., subprocess.CompletedProcess[str]],
    database: str,
    remote_root: Path,
) -> None:
    assert run_script(BACKUP).returncode == 0
    [dump] = uploaded(remote_root, "baba-db-*.sql.gz.gpg")
    assert psql(database, "update players set name = 'DEPOIS'").returncode == 0

    result = run_script(RESTORE, str(dump), BABA_RESTORE_YES="0")
    assert result.returncode != 0
    assert psql(database, "select distinct name from players").stdout.strip() == "DEPOIS"


def test_a_failed_dump_is_an_error_and_leaves_nothing_behind(
    run_script: Callable[..., subprocess.CompletedProcess[str]],
    remote_root: Path,
    tmp_path: Path,
) -> None:
    result = run_script(BACKUP, BABA_DB_EXEC="docker exec -i no-such-container")
    assert result.returncode != 0
    assert not (remote_root / "bucket").exists()
    assert not (tmp_path / "staging" / "last-success").exists()
    assert not list((tmp_path / "staging").glob("baba-*"))


def test_a_missing_passphrase_file_is_reported(
    run_script: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = run_script(BACKUP, BABA_BACKUP_PASSPHRASE_FILE="/nonexistent")
    assert result.returncode != 0
    assert "passphrase" in result.stderr


def test_the_simplest_setup_needs_no_encryption_and_no_remote(
    run_script: Callable[..., subprocess.CompletedProcess[str]],
    database: str,
    tmp_path: Path,
) -> None:
    plain_env = {"BABA_BACKUP_PASSPHRASE_FILE": "", "BABA_BACKUP_REMOTE": ""}
    result = run_script(BACKUP, **plain_env)
    assert result.returncode == 0, result.stderr
    [dump] = (tmp_path / "staging").glob("baba-db-*.sql.gz")
    assert dump.name.endswith(".sql.gz")  # no .gpg
    assert (tmp_path / "staging" / "last-success").exists()

    assert psql(database, "delete from payments; update players set name = 'X'").returncode == 0
    restored = run_script(RESTORE, str(dump), **plain_env)
    assert restored.returncode == 0, restored.stderr
    assert psql(database, "select sum(amount) from payments").stdout.strip() == "130.50"
    assert (
        psql(database, "select count(*) from players where name = 'Fernando'").stdout.strip() == "1"
    )


def test_it_refuses_to_run_when_the_external_disk_is_not_mounted(
    run_script: Callable[..., subprocess.CompletedProcess[str]], tmp_path: Path
) -> None:
    not_a_mount = tmp_path / "disco-externo"
    not_a_mount.mkdir()
    result = run_script(
        BACKUP,
        BABA_BACKUP_MOUNTPOINT=str(not_a_mount),
        BABA_BACKUP_DIR=str(not_a_mount / "baba"),
    )
    assert result.returncode != 0
    assert "not mounted" in result.stderr
    assert not (not_a_mount / "baba").exists()


def test_a_missing_receipts_volume_is_skipped(
    run_script: Callable[..., subprocess.CompletedProcess[str]], tmp_path: Path
) -> None:
    result = run_script(BACKUP, BABA_BACKUP_RECEIPTS_VOLUME="baba-no-such-volume")
    assert result.returncode == 0, result.stderr
    assert not list((tmp_path / "staging").glob("baba-receipts-*"))


def test_retention_removes_old_backups_only_after_a_new_upload(
    run_script: Callable[..., subprocess.CompletedProcess[str]],
    remote_root: Path,
    tmp_path: Path,
) -> None:
    old_name = "baba-db-20200101T000000Z.sql.gz.gpg"
    staging = tmp_path / "staging"
    staging.mkdir()
    bucket = remote_root / "bucket"
    bucket.mkdir(parents=True)
    long_ago = time.time() - 90 * 86400
    for path in (staging / old_name, bucket / old_name):
        path.write_bytes(b"old")
        os.utime(path, (long_ago, long_ago))

    result = run_script(BACKUP, BABA_BACKUP_RETENTION_DAYS="30")
    assert result.returncode == 0, result.stderr
    assert not (staging / old_name).exists()
    assert not (bucket / old_name).exists()
    assert len(uploaded(remote_root, "baba-db-2*.sql.gz.gpg")) == 1


def in_volume(volume: str, command: str) -> subprocess.CompletedProcess[str]:
    return sh("docker", "run", "--rm", "-v", f"{volume}:/data", "alpine:3", "sh", "-c", command)


def test_receipts_volume_is_archived_and_restored(
    run_script: Callable[..., subprocess.CompletedProcess[str]],
    remote_root: Path,
) -> None:
    volume = f"baba-backup-test-{uuid.uuid4().hex[:8]}"
    receipt = "/data/baba-comprovantes/a.png"
    try:
        assert sh("docker", "volume", "create", volume).returncode == 0
        prepare = f"mkdir -p /data/baba-comprovantes && echo pix > {receipt}"
        assert in_volume(volume, prepare).returncode == 0

        result = run_script(BACKUP, BABA_BACKUP_RECEIPTS_VOLUME=volume)
        assert result.returncode == 0, result.stderr
        [archive] = uploaded(remote_root, "baba-receipts-*.tar.gz.gpg")
        assert b"pix" not in archive.read_bytes()

        assert in_volume(volume, "rm -rf /data/baba-comprovantes").returncode == 0
        restored = run_script(RESTORE, str(archive), BABA_BACKUP_RECEIPTS_VOLUME=volume)
        assert restored.returncode == 0, restored.stderr
        assert in_volume(volume, f"cat {receipt}").stdout.strip() == "pix"
    finally:
        sh("docker", "volume", "rm", "-f", volume)


@pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone is not installed")
def test_backup_works_with_the_real_rclone_and_a_local_remote(
    run_script: Callable[..., subprocess.CompletedProcess[str]], tmp_path: Path
) -> None:
    remote = tmp_path / "real-remote"
    remote.mkdir()
    stale = remote / "baba-db-20200101T000000Z.sql.gz.gpg"
    stale.write_bytes(b"old")
    long_ago = time.time() - 90 * 86400
    os.utime(stale, (long_ago, long_ago))

    result = run_script(BACKUP, BABA_RCLONE="rclone", BABA_BACKUP_REMOTE=str(remote))
    assert result.returncode == 0, result.stderr
    assert not stale.exists()
    assert len(list(remote.glob("baba-db-*.sql.gz.gpg"))) == 1
