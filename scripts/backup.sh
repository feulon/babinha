#!/usr/bin/env bash
# Backup of the baba: a compressed pg_dump of the database plus, when the receipts live in a
# local volume, a tar of that volume. Copies go to a folder (use an external disk) and old
# ones are removed. Encryption and an upload to R2 are optional. Run it by hand, from
# `scripts/baba.sh backup`, or from a timer (docs/producao.md).
#
# Settings (environment, or BABA_BACKUP_* lines in .env.production), all optional:
#   BABA_BACKUP_DIR               destination folder, default ./backups
#   BABA_BACKUP_MOUNTPOINT        refuse to run unless this folder is a mounted disk
#   BABA_BACKUP_RETENTION_DAYS    default 30
#   BABA_BACKUP_RECEIPTS_VOLUME   docker volume with the receipts (local-receipts setup)
#   BABA_BACKUP_PASSPHRASE_FILE   encrypt with gpg using the passphrase in this file
#   BABA_BACKUP_REMOTE            also upload with rclone, e.g. r2:baba-backups
# Exit code is non-zero on any failure, which systemd records.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=scripts/backup-lib.sh
. scripts/backup-lib.sh

load_env_file
setup_commands
DIR="${BABA_BACKUP_DIR:-./backups}"
REMOTE="${BABA_BACKUP_REMOTE:-}"
DAYS="${BABA_BACKUP_RETENTION_DAYS:-30}"
case "$DAYS" in ''|*[!0-9]*) die "BABA_BACKUP_RETENTION_DAYS must be a number of days" ;; esac
[ "$DAYS" -ge 1 ] || die "BABA_BACKUP_RETENTION_DAYS must be at least 1"
if [ -n "${BABA_BACKUP_MOUNTPOINT:-}" ] && ! mountpoint -q "$BABA_BACKUP_MOUNTPOINT"; then
    die "$BABA_BACKUP_MOUNTPOINT is not mounted (is the external disk plugged in?)"
fi

mkdir -p "$DIR"
chmod 700 "$DIR"
umask 077
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
EXT=""
[ -z "$PASSPHRASE_FILE" ] || EXT=".gpg"
DB_FILE="$DIR/baba-db-$STAMP.sql.gz$EXT"
RECEIPTS_FILE="$DIR/baba-receipts-$STAMP.tar.gz$EXT"
trap 'rm -f "$DB_FILE.partial" "$RECEIPTS_FILE.partial"' EXIT

log "dumping the database"
"${DB_EXEC[@]}" pg_dump -U baba -d baba --clean --if-exists --no-owner --no-privileges \
    | gzip -9 | encrypt > "$DB_FILE.partial"
# A dump that cannot be read back, or was cut short, is not a backup: check before keeping it.
decrypt_file "$DB_FILE.partial" "$DB_FILE" | gunzip | tail -n 5 | grep -q "PostgreSQL database dump complete" \
    || die "the dump failed verification (not readable or incomplete)"
mv "$DB_FILE.partial" "$DB_FILE"
log "database backup ok: $(basename "$DB_FILE") ($(wc -c < "$DB_FILE") bytes)"

if [ -n "${BABA_BACKUP_RECEIPTS_VOLUME:-}" ]; then
    # The volume only exists with the local-receipts setup; skip quietly when receipts are in R2.
    if docker volume inspect "$BABA_BACKUP_RECEIPTS_VOLUME" > /dev/null 2>&1; then
        log "archiving the receipts volume $BABA_BACKUP_RECEIPTS_VOLUME"
        docker run --rm -v "$BABA_BACKUP_RECEIPTS_VOLUME":/data:ro "$TOOLS_IMAGE" \
            tar czf - -C /data . | encrypt > "$RECEIPTS_FILE.partial"
        decrypt_file "$RECEIPTS_FILE.partial" "$RECEIPTS_FILE" | tar tzf - > /dev/null \
            || die "the receipts archive failed verification"
        mv "$RECEIPTS_FILE.partial" "$RECEIPTS_FILE"
        log "receipts backup ok: $(basename "$RECEIPTS_FILE") ($(wc -c < "$RECEIPTS_FILE") bytes)"
    else
        log "volume $BABA_BACKUP_RECEIPTS_VOLUME not found, skipping the receipts copy"
    fi
fi

if [ -n "$REMOTE" ]; then
    log "uploading to $REMOTE"
    "${RCLONE[@]}" copy "$DIR" "$REMOTE" --include "baba-*.gz" --include "baba-*.gpg"
fi

# Prune only after the new files are safely stored, so a long outage never empties the history.
log "removing backups older than $DAYS days"
find "$DIR" -maxdepth 1 \( -name 'baba-*.gz' -o -name 'baba-*.gpg' \) -mtime "+$DAYS" -delete
if [ -n "$REMOTE" ]; then
    "${RCLONE[@]}" delete "$REMOTE" --min-age "${DAYS}d" --include "baba-*.gz" --include "baba-*.gpg"
fi

date -u +%Y-%m-%dT%H:%M:%SZ > "$DIR/last-success"
log "backup finished: $STAMP"
