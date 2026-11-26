#!/usr/bin/env bash
# Applique les migrations sur un Postgres jetable et lance les tests SQL (isolation, RLS, fonctions atomiques).
# Usage : supabase/tests/run.sh   (Postgres 15+ : initdb/pg_ctl dans le PATH, ou PG_BIN=/usr/lib/postgresql/16/bin)
set -euo pipefail
cd "$(dirname "$0")/.."
BIN="${PG_BIN:-$(dirname "$(command -v initdb 2>/dev/null || echo /usr/lib/postgresql/16/bin/initdb)")}"
DIR="$(mktemp -d)"
PORT="${PG_PORT:-54329}"
trap '"$BIN/pg_ctl" -D "$DIR/data" stop -m immediate >/dev/null 2>&1 || true; rm -rf "$DIR"' EXIT
"$BIN/initdb" -D "$DIR/data" -U postgres -A trust >/dev/null
"$BIN/pg_ctl" -D "$DIR/data" -o "-p $PORT -k $DIR -c listen_addresses=''" -l "$DIR/log" start >/dev/null
PSQL=(psql -X -q -v ON_ERROR_STOP=1 -h "$DIR" -p "$PORT" -U postgres -d postgres)
"${PSQL[@]}" -f tests/00_simulacre_supabase.sql
for m in migrations/*.sql; do "${PSQL[@]}" -f "$m"; done
"${PSQL[@]}" -f tests/10_isolation_et_corrections.sql
