#!/usr/bin/env bash
# Восстанавливает копию во временную базу и сверяет её с рабочей. Рабочую базу не меняет.
set -euo pipefail

dir="${BACKUP_DIR:-backups}"
file="${1:-$(ls -1t "$dir"/adhelper-*.dump 2>/dev/null | head -1)}"
if [ -z "$file" ] || [ ! -s "$file" ]; then
  echo "Копия не найдена: $dir" >&2
  exit 1
fi

check_db="adhelper_restore_check_$$"
psql() { docker compose exec -T db psql -U adhelper -v ON_ERROR_STOP=1 -qtA "$@"; }
trap 'psql -d postgres -c "DROP DATABASE IF EXISTS $check_db WITH (FORCE)" > /dev/null' EXIT

psql -d postgres -c "CREATE DATABASE $check_db" > /dev/null
docker compose exec -T db pg_restore -U adhelper -d "$check_db" --no-owner --exit-on-error < "$file"

# Журнал и миграции обязаны совпасть. Строк в рабочей базе может стать больше после копии.
version_copy="$(psql -d "$check_db" -c 'SELECT version_num FROM alembic_version')"
changes_copy="$(psql -d "$check_db" -c 'SELECT count(*) FROM changes')"
changes_live="$(psql -d adhelper -c 'SELECT count(*) FROM changes')"
trigger="$(psql -d "$check_db" -c "SELECT count(*) FROM pg_trigger WHERE tgrelid = 'changes'::regclass AND NOT tgisinternal")"

if [ "$changes_copy" -gt "$changes_live" ]; then
  echo "В копии записей журнала больше, чем в рабочей базе: $changes_copy и $changes_live" >&2
  exit 1
fi
if [ "$trigger" -lt 1 ]; then
  echo "В копии нет защиты журнала от изменений" >&2
  exit 1
fi
echo "Копия $file читается: версия схемы $version_copy, записей журнала $changes_copy из $changes_live."
