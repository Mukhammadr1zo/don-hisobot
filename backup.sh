#!/usr/bin/env bash
# Database backup for the compose.yaml stack. Run by cron at 10:00 and 22:00 (see README.md).
# Writes backups/hisobot-YYYY-MM-DD_HHMM.dump, checks it, deletes dumps older than KEEP_DAYS.
# Any failure exits non-zero and leaves no half-written dump behind.
set -euo pipefail
# A dump holds the PIN and password hashes: owner-only.
umask 077

cd "$(dirname "$0")"
BACKUP_DIR="${BACKUP_DIR:-backups}"
KEEP_DAYS="${KEEP_DAYS:-30}"
# An empty database dumps to about 45 KB (PostgreSQL 17); anything smaller is not a real backup.
MIN_BYTES="${MIN_BYTES:-20000}"

mkdir -p "$BACKUP_DIR"
file="$BACKUP_DIR/hisobot-$(TZ=Asia/Tashkent date +%Y-%m-%d_%H%M).dump"
tmp="$file.part"
trap 'rm -f "$tmp"' EXIT

# Sessions are not worth restoring: everyone signs in again.
docker compose exec -T db pg_dump -U hisobot -d hisobot -Fc --exclude-table-data=django_session > "$tmp"

size=$(stat -c %s "$tmp")
if (( size < MIN_BYTES )); then
  echo "backup.sh: $tmp is only $size bytes" >&2
  exit 1
fi
docker compose exec -T db pg_restore --list < "$tmp" > /dev/null

mv "$tmp" "$file"
find "$BACKUP_DIR" -maxdepth 1 -name 'hisobot-*.dump' -mtime +"$KEEP_DAYS" -delete
echo "$file $size"
