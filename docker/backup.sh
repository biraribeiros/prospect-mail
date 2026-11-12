#!/bin/sh
# Backup diário do banco (pg_dump comprimido). Guarda 30 dias em /backups. Roda 1x ao subir e depois a cada 24 h.
set -eu
DIR=/backups
mkdir -p "$DIR"
while true; do
  STAMP=$(date -u +%Y%m%d-%H%M%S)
  OUT="$DIR/prospector-$STAMP.sql.gz"
  if pg_dump -h postgres -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$OUT.tmp"; then
    mv "$OUT.tmp" "$OUT"
    echo "backup ok: $OUT"
  else
    rm -f "$OUT.tmp"
    echo "BACKUP FALHOU" >&2
  fi
  find "$DIR" -name 'prospector-*.sql.gz' -mtime +30 -delete
  sleep 86400
done
