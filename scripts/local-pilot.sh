#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
state_dir="$PWD/.local-pilot"
data_dir="$state_dir/postgres"
pg_port=5438
app_port=8765
db_user=quickly_pilot
db_name=quickly_pilot

case "${1:-}" in
  start|run)
    mkdir -p "$state_dir"
    chmod 700 "$state_dir"
    if [[ ! -f "$state_dir/db-password" ]]; then
      .venv/bin/python -c 'import secrets; print(secrets.token_hex(24))' >"$state_dir/db-password"
    fi
    chmod 600 "$state_dir/db-password"
    export PGPASSWORD="$(cat "$state_dir/db-password")"
    if [[ ! -f "$data_dir/PG_VERSION" ]]; then
      initdb -D "$data_dir" -U "$db_user" --auth-local=scram-sha-256 --auth-host=scram-sha-256 --pwfile="$state_dir/db-password" >/dev/null
    fi
    if ! pg_ctl -D "$data_dir" status >/dev/null 2>&1; then
      pg_ctl -D "$data_dir" -o "-c listen_addresses=127.0.0.1 -c unix_socket_directories='' -p $pg_port" -l "$state_dir/postgres.log" start >/dev/null
    fi
    if [[ "$(psql -h 127.0.0.1 -p "$pg_port" -U "$db_user" -d postgres -Atqc "SELECT 1 FROM pg_database WHERE datname='$db_name'")" != 1 ]]; then
      createdb -h 127.0.0.1 -p "$pg_port" -U "$db_user" "$db_name"
    fi
    if [[ ! -x .venv/bin/uvicorn || ! -f frontend/dist/index.html ]]; then
      echo "Install Python requirements and build frontend first." >&2
      exit 1
    fi
    if [[ -f "$state_dir/app.pid" ]] && kill -0 "$(cat "$state_dir/app.pid")" 2>/dev/null; then
      echo "App already running at http://127.0.0.1:$app_port"
      exit 0
    fi
    export DATABASE_URL="postgresql+asyncpg://$db_user:$PGPASSWORD@127.0.0.1:$pg_port/$db_name"
    unset PGPASSWORD
    export BASE_URL="http://127.0.0.1:$app_port"
    export CORS_ORIGINS="$BASE_URL"
    export QUICKLY_TELEMETRY=false
    export QUICKLY_TELEMETRY_URL=http://127.0.0.1:9
    export QUICKLY_VERSION=unknown
    export QUICKLY_MODE=development
    export TZ=UTC
    export TEST_MODE="${TEST_MODE:-true}"
    if [[ -f "$state_dir/zapbox.env" ]]; then
      set -a
      source "$state_dir/zapbox.env"
      set +a
    fi
    if [[ ! -f "$state_dir/secret-key" ]]; then
      .venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(64))' >"$state_dir/secret-key"
    fi
    if [[ ! -f "$state_dir/encryption-key" ]]; then
      .venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' >"$state_dir/encryption-key"
    fi
    chmod 600 "$state_dir/secret-key" "$state_dir/encryption-key"
    export QUICKLY_SECRET_KEY="$(cat "$state_dir/secret-key")"
    export QUICKLY_ENCRYPTION_KEY="$(cat "$state_dir/encryption-key")"
    if [[ "${1:-}" == run ]]; then
      echo $$ >"$state_dir/app.pid"
      exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$app_port"
    fi
    nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$app_port" >"$state_dir/app.log" 2>&1 &
    echo $! >"$state_dir/app.pid"
    echo "Starting at http://127.0.0.1:$app_port"
    ;;
  stop)
    if [[ -f "$state_dir/app.pid" ]] && kill -0 "$(cat "$state_dir/app.pid")" 2>/dev/null; then
      kill "$(cat "$state_dir/app.pid")"
      rm "$state_dir/app.pid"
    fi
    if [[ -f "$data_dir/PG_VERSION" ]] && pg_ctl -D "$data_dir" status >/dev/null 2>&1; then
      pg_ctl -D "$data_dir" stop -m fast >/dev/null
    fi
    ;;
  status)
    if [[ -f "$state_dir/app.pid" ]] && kill -0 "$(cat "$state_dir/app.pid")" 2>/dev/null; then
      echo "App running at http://127.0.0.1:$app_port"
    else
      echo "App stopped"
    fi
    pg_ctl -D "$data_dir" status 2>/dev/null || true
    ;;
  *)
    echo "Usage: scripts/local-pilot.sh start|run|stop|status" >&2
    exit 2
    ;;
esac
