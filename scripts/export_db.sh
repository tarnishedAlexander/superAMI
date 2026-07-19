#!/usr/bin/env bash
# Exporta la DB procesada completa (trámites + embeddings + relacionados +
# catálogos) a un solo archivo compartible, para que otra persona no tenga
# que correr ingest.load / ingest.relacionados de nuevo.
#
# Uso:
#   scripts/export_db.sh                  # genera ami-YYYYMMDD.dump
#   scripts/export_db.sh mi_archivo.dump  # nombre custom
set -euo pipefail
cd "$(dirname "$0")/.."
ARCHIVO="${1:-ami-$(date +%Y%m%d).dump}"
docker compose exec -T db pg_dump -U ami -Fc ami > "$ARCHIVO"
echo "listo: $ARCHIVO ($(du -h "$ARCHIVO" | cut -f1))"
echo "compartir este archivo; el otro lado corre: scripts/import_db.sh $ARCHIVO"
