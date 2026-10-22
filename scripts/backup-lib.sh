# Shared by backup.sh and restore.sh (sourced, not executed).
# shellcheck shell=bash disable=SC2034

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# Loads BABA_BACKUP_* settings from the production env file without overriding what is already
# exported, so cron/systemd and manual runs behave the same.
load_env_file() {
    local file="${BABA_ENV_FILE:-.env.production}" line key value
    [ -f "$file" ] || return 0
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            BABA_BACKUP_*=*|BABA_COMPOSE=*|BABA_DB_EXEC=*)
                key="${line%%=*}"
                value="${line#*=}"
                value="${value%\"}"; value="${value#\"}"
                value="${value%\'}"; value="${value#\'}"
                if [ -z "${!key:-}" ]; then export "$key=$value"; fi
                ;;
        esac
    done < "$file"
}

setup_commands() {
    COMPOSE="${BABA_COMPOSE:-docker compose --env-file ${BABA_ENV_FILE:-.env.production} -f compose.prod.yaml}"
    # Command prefix that runs a program inside the database container.
    read -r -a DB_EXEC <<< "${BABA_DB_EXEC:-$COMPOSE exec -T db}"
    read -r -a RCLONE <<< "${BABA_RCLONE:-rclone}"
    TOOLS_IMAGE="${BABA_BACKUP_TOOLS_IMAGE:-alpine:3}"
    PASSPHRASE_FILE="${BABA_BACKUP_PASSPHRASE_FILE:-}"
    if [ -n "$PASSPHRASE_FILE" ] && [ ! -s "$PASSPHRASE_FILE" ]; then
        die "passphrase file $PASSPHRASE_FILE is missing or empty"
    fi
}

# Encryption is optional: with a passphrase file the data is encrypted (AES-256, gpg) and the
# file name ends in .gpg; without one the stream passes through untouched.
# Both read stdin and write stdout.
encrypt() {
    if [ -n "$PASSPHRASE_FILE" ]; then
        gpg --batch --quiet --pinentry-mode loopback --passphrase-file "$PASSPHRASE_FILE" \
            --symmetric --cipher-algo AES256 --compress-algo none
    else
        cat
    fi
}

# Decrypts when the file name ends in .gpg (needs the passphrase), passes the file through
# otherwise. The optional second argument is the name to judge by (for a .partial file).
decrypt_file() {
    case "${2:-$1}" in
        *.gpg)
            [ -n "$PASSPHRASE_FILE" ] || die "$1 is encrypted: set BABA_BACKUP_PASSPHRASE_FILE"
            gpg --batch --quiet --pinentry-mode loopback --passphrase-file "$PASSPHRASE_FILE" \
                --decrypt < "$1"
            ;;
        *) cat "$1" ;;
    esac
}
