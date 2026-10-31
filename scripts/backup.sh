#!/usr/bin/env bash
# Резервная копия базы в BACKUP_DIR. Ключи шифрования в неё не входят: храните их отдельно.
set -euo pipefail

dir="${BACKUP_DIR:-backups}"
keep_days="${BACKUP_KEEP_DAYS:-14}"
mkdir -p "$dir"
chmod 700 "$dir"
file="$dir/adhelper-$(date -u +%Y%m%dT%H%M%SZ).dump"
partial="$file.partial"
trap 'rm -f "$partial"' EXIT

umask 077
docker compose exec -T db pg_dump -U adhelper -d adhelper --format=custom > "$partial"
# Пустой или обрезанный файл не должен выглядеть как копия.
docker compose exec -T db pg_restore --list < "$partial" > /dev/null
mv "$partial" "$file"
find "$dir" -name 'adhelper-*.dump' -mtime "+$keep_days" -delete
echo "$file"
