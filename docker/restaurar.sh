#!/bin/sh
# Restaura um backup. Uso: docker compose run --rm -v ./data/backups:/backups backup sh /app/restaurar.sh /backups/ARQUIVO.sql.gz
# ATENÇÃO: apaga o conteúdo atual do banco.
set -eu
FILE="$1"
[ -f "$FILE" ] || { echo "arquivo não encontrado: $FILE"; exit 1; }
psql -h postgres -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\"" -c "CREATE DATABASE \"$POSTGRES_DB\""
gunzip -c "$FILE" | psql -h postgres -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q
echo "restaurado: $FILE"
