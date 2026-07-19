#!/usr/bin/env bash
# Importa un dump generado por export_db.sh en la DB local del proyecto.
# Reemplaza los datos existentes (--clean). No necesita API key ni ingesta.
#
# Requisitos previos: docker compose up -d   (la DB tiene que estar corriendo)
#
# Uso:
#   scripts/import_db.sh ami-20260717.dump
set -euo pipefail
cd "$(dirname "$0")/.."
ARCHIVO="${1:?uso: scripts/import_db.sh <archivo.dump>}"
docker compose exec -T db pg_restore -U ami -d ami --clean --if-exists --no-owner < "$ARCHIVO"
echo "importado. verificando:"
docker compose exec -T db psql -U ami -d ami -c \
  "SELECT (SELECT count(*) FROM tramites) AS tramites,
          (SELECT count(embedding) FROM tramites) AS con_embedding,
          (SELECT count(*) FROM tramites_relacionados) AS relacionados;"
