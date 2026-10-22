#!/bin/sh
# Apply pending migrations before starting the app (safe to run on every start).
set -eu
if [ "${BABA_MIGRATE_ON_START:-true}" = "true" ]; then
    python /app/manage.py migrate --noinput
fi
exec "$@"
