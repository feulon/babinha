#!/usr/bin/env bash
# Restores a backup made by backup.sh. Pass the file from the backup folder:
#   scripts/restore.sh /mnt/disco-externo/baba-db-20261012T063000Z.sql.gz
# baba-db-*  replaces the contents of the database (stop the app first, see docs/producao.md).
# baba-receipts-*  unpacks the receipts into BABA_BACKUP_RECEIPTS_VOLUME (local-receipts setup).
# Files ending in .gpg need BABA_BACKUP_PASSPHRASE_FILE. Asks for confirmation;
# BABA_RESTORE_YES=1 skips the question (used by the tests).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=scripts/backup-lib.sh
. scripts/backup-lib.sh

[ "$#" -eq 1 ] || die "usage: scripts/restore.sh <baba-db-*.sql.gz[.gpg] | baba-receipts-*.tar.gz[.gpg]>"
FILE="$1"
[ -f "$FILE" ] || die "file not found: $FILE"

load_env_file
setup_commands

confirm() {
    [ "${BABA_RESTORE_YES:-}" = "1" ] && return 0
    printf '%s\nType "restaurar" to continue: ' "$1" >&2
    read -r answer
    [ "$answer" = "restaurar" ] || die "cancelled"
}

case "$(basename "$FILE")" in
    baba-db-*.sql.gz|baba-db-*.sql.gz.gpg)
        confirm "This REPLACES everything in the baba database with the contents of $FILE."
        log "restoring the database"
        decrypt_file "$FILE" | gunzip \
            | "${DB_EXEC[@]}" psql -U baba -d baba -q -v ON_ERROR_STOP=1 --single-transaction \
                > /dev/null
        log "database restored"
        ;;
    baba-receipts-*.tar.gz|baba-receipts-*.tar.gz.gpg)
        VOLUME="${BABA_BACKUP_RECEIPTS_VOLUME:-}"
        [ -n "$VOLUME" ] || die "BABA_BACKUP_RECEIPTS_VOLUME is not set"
        confirm "This writes the receipts from $FILE into the volume $VOLUME."
        log "restoring the receipts"
        decrypt_file "$FILE" | docker run --rm -i -v "$VOLUME":/data "$TOOLS_IMAGE" \
            tar xzf - -C /data
        log "receipts restored"
        ;;
    *) die "unrecognised file name (expected baba-db-*.sql.gz[.gpg] or baba-receipts-*.tar.gz[.gpg])" ;;
esac
