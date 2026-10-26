#!/usr/bin/env bash
# One command for the production stack on the notebook (docs/producao.md).
#
#   scripts/baba.sh subir        create .env.production on the first run, build and start
#   scripts/baba.sh presidente   create the first account (asks for the password in the terminal)
#   scripts/baba.sh logs [svc]   follow the logs (app by default)
#   scripts/baba.sh status       state of each service
#   scripts/baba.sh parar        stop everything (data is kept)
#   scripts/baba.sh atualizar    backup, git pull, rebuild and restart
#   scripts/baba.sh backup       run scripts/backup.sh
#   scripts/baba.sh apagar-jogador CELULAR [--confirmar]
#                                erase a player and all their data (LGPD); simulates by default
#   scripts/baba.sh lista [--data DD/MM/AAAA]
#                                print the Sunday list as text for WhatsApp (needs no internet or tunnel)
#   scripts/baba.sh admin --email EMAIL [--remover]
#                                turn the hidden admin role on (or off) for an account
#   scripts/baba.sh init         only create .env.production
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

ENV_FILE="${BABA_ENV_FILE:-.env.production}"

die() { echo "ERROR: $*" >&2; exit 1; }

# Replaces the value of KEY= in the env file (the key must already be there).
set_var() {
    local key="$1" value="$2"
    grep -q "^$key=" "$ENV_FILE" || die "$key is missing from $ENV_FILE"
    # | is the sed delimiter; none of the generated values contains it.
    sed -i "s|^$key=.*|$key=$value|" "$ENV_FILE"
}

lan_ip() {
    local ip
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    echo "${ip:-192.0.2.10}"
}

init_env() {
    if [ -f "$ENV_FILE" ]; then
        return 0
    fi
    command -v openssl > /dev/null || die "openssl is needed to generate the secrets"
    cp .env.production.example "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    set_var DJANGO_SECRET_KEY "$(openssl rand -hex 40)"
    set_var POSTGRES_PASSWORD "$(openssl rand -hex 24)"
    set_var BABA_S3_ACCESS_KEY_ID "$(openssl rand -hex 8)"
    set_var BABA_S3_SECRET_ACCESS_KEY "$(openssl rand -hex 24)"
    set_var BABA_INVITE_CODE "$(openssl rand -hex 4)"
    set_var DJANGO_ALLOWED_HOSTS "$(lan_ip),localhost"
    echo "Created $ENV_FILE. Players will open http://$(lan_ip) (the notebook's IP on this network)." >&2
    echo "Sign-up code for the group: $(env_value BABA_INVITE_CODE) (BABA_INVITE_CODE in $ENV_FILE)." >&2
    echo "Reserve this IP for the notebook in the router so it does not change." >&2
}

env_value() {
    grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- | tr -d '"' || true
}

compose() {
    local files=(-f compose.prod.yaml)
    if [ -n "$(env_value TUNNEL_TOKEN)" ]; then
        files+=(-f compose.prod.tunnel.yaml)
    fi
    docker compose --env-file "$ENV_FILE" "${files[@]}" "$@"
}

# Where scripts/backup.sh writes, and when it last did: shown before erasing a player.
last_backup() {
    local dir file latest=""
    dir="${BABA_BACKUP_DIR:-$(env_value BABA_BACKUP_DIR)}"
    dir="${dir:-./backups}"
    for file in "$dir"/baba-db-*.sql.gz*; do
        [ -f "$file" ] || continue
        if [ -z "$latest" ] || [ "$file" -nt "$latest" ]; then latest="$file"; fi
    done
    if [ -n "$latest" ]; then
        echo "Ultimo backup: $(date -r "$latest" '+%d/%m/%Y %H:%M') ($latest)." >&2
    else
        echo "Nenhum backup em $dir." >&2
    fi
}

main() {
    local command="${1:-}"
    [ "$#" -gt 0 ] && shift
    case "$command" in
        init)
            init_env
            ;;
        subir)
            init_env
            compose up -d --build
            compose ps
            local host
            host="$(env_value DJANGO_ALLOWED_HOSTS | cut -d, -f1)"
            if [ -n "$(env_value TUNNEL_TOKEN)" ]; then
                echo "Pronto. Abra https://$host/saude/ (de fora da wifi de casa) para conferir o tunel." >&2
            else
                local port suffix=""
                port="$(env_value APP_PORT)"
                [ -z "$port" ] || [ "$port" = "80" ] || suffix=":$port"
                echo "Pronto. Abra http://$host$suffix no celular (mesma wifi)." >&2
            fi
            ;;
        presidente)
            [ -f "$ENV_FILE" ] || die "run scripts/baba.sh subir first"
            if [ "$#" -eq 0 ]; then
                read -r -p "Celular com DDD (e o login): " phone
                read -r -p "E-mail: " email
                read -r -p "Nome: " name
                read -r -p "Apelido (vazio usa o nome): " nickname
                set -- --telefone "$phone" --email "$email" --nome "$name"
                [ -z "$nickname" ] || set -- "$@" --apelido "$nickname"
            fi
            compose exec app python manage.py criar_presidente "$@"
            ;;
        logs)
            compose logs -f --tail 100 "${1:-app}"
            ;;
        status)
            compose ps
            ;;
        parar)
            compose stop
            ;;
        atualizar)
            [ -f "$ENV_FILE" ] || die "run scripts/baba.sh subir first"
            scripts/backup.sh
            git pull --ff-only
            compose up -d --build
            compose ps
            ;;
        backup)
            scripts/backup.sh
            ;;
        apagar-jogador)
            [ -f "$ENV_FILE" ] || die "run scripts/baba.sh subir first"
            [ "$#" -gt 0 ] || die "usage: scripts/baba.sh apagar-jogador CELULAR [--confirmar]"
            last_backup
            # Without a terminal the command refuses --confirmar unless --sim-tenho-certeza.
            local exec_flags=()
            [ -t 0 ] || exec_flags=(-T)
            compose exec "${exec_flags[@]}" app python manage.py apagar_jogador "$@"
            ;;
        lista)
            [ -f "$ENV_FILE" ] || die "run scripts/baba.sh subir first"
            # -T keeps the text clean (no terminal line endings) when it is piped or copied.
            local list_flags=()
            { [ -t 0 ] && [ -t 1 ]; } || list_flags=(-T)
            compose exec "${list_flags[@]}" app python manage.py lista_whatsapp "$@"
            ;;
        admin)
            [ -f "$ENV_FILE" ] || die "run scripts/baba.sh subir first"
            [ "$#" -gt 0 ] || die "usage: scripts/baba.sh admin --email EMAIL [--remover]"
            local admin_flags=()
            [ -t 0 ] || admin_flags=(-T)
            compose exec "${admin_flags[@]}" app python manage.py definir_admin "$@"
            ;;
        *)
            sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            [ -z "$command" ] || exit 1
            ;;
    esac
}

main "$@"
