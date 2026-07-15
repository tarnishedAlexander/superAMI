"""Sync incremental contra tramites-bo (el dataset se actualiza los domingos).

Uso:
    python -m ingest.sync             # descarga, aplica diff, upserta cambiados, marca bajas
    python -m ingest.sync --dry-run   # solo reporta qué haría
    python -m ingest.sync --jsonl ruta/local.jsonl

Agendable con cron / Task Scheduler; idempotente (correrlo dos veces no repite trabajo).
"""
import argparse
import logging
from datetime import date

from dotenv import load_dotenv

from db.connection import get_connection
from db.queries import (
    guardar_sync_state,
    guardar_tramite_completo,
    leer_estado_tramites,
    marcar_activos,
    marcar_inactivos,
)
from ingest.costo_llm import extraer_costo
from ingest.load import leer_registros
from ingest.mapper import mapear_tramite, parsear_fecha, texto_para_embedding
from providers import factory

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def diff_registros(registros: list[dict], estado_db: dict[int, dict]) -> tuple[list[dict], set[int]]:
    """Diff contra el estado de la DB. Devuelve (cambiados, bajas).

    - cambiados: ids nuevos, inactivos que reaparecen en el jsonl, o con
      fechaActualización posterior a last_updated (o last_updated nulo).
      Sin fecha parseable en el jsonl no hay señal de cambio: se salta
      (idempotencia — no re-procesar lo mismo cada semana).
    - bajas: ids activos en la DB que ya no están en el jsonl.
    """
    cambiados = []
    ids_jsonl: set[int] = set()
    for registro in registros:
        rid = int(registro["id"])
        ids_jsonl.add(rid)
        estado = estado_db.get(rid)
        fecha = parsear_fecha(registro.get("fechaActualización"))
        if estado is None or not estado["activo"]:
            cambiados.append(registro)
        elif fecha is not None and (estado["last_updated"] is None or fecha > estado["last_updated"]):
            cambiados.append(registro)
    bajas = {rid for rid, e in estado_db.items() if e["activo"] and rid not in ids_jsonl}
    return cambiados, bajas


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--jsonl", default=None, help="ruta local a tramites.jsonl (default: descarga)")
    parser.add_argument("--dry-run", action="store_true", help="solo reportar, sin escribir")
    args = parser.parse_args()

    registros = leer_registros(args.jsonl)
    with get_connection() as conn:
        estado_db = leer_estado_tramites(conn)
    cambiados, bajas = diff_registros(registros, estado_db)
    logger.info("jsonl: %d registros | cambiados: %d | bajas: %d", len(registros), len(cambiados), len(bajas))
    if args.dry_run:
        for r in cambiados[:20]:
            logger.info("  cambiado: %s %s", r["id"], r.get("nombre", "")[:60])
        for rid in sorted(bajas)[:20]:
            logger.info("  baja: %s", rid)
        return

    filas = []
    for registro in cambiados:
        try:
            filas.append(mapear_tramite(registro))
        except Exception:
            logger.exception("registro %s falló en el mapeo, se salta", registro.get("id"))

    if filas:
        chat = factory.chat_potente()
        for fila in filas:
            if not fila["necesita_llm"]:
                continue
            datos = extraer_costo(chat, fila["descripcion"], fila["resultado"])
            if datos:
                fila.update(
                    costo_monto=datos["monto"], costo_moneda=datos["moneda"], costo_concepto=datos["concepto"]
                )

        emb = factory.embedder()
        logger.info("generando %d embeddings...", len(filas))
        vectores = emb.embed_documents([texto_para_embedding(f) for f in filas])
    else:
        vectores = []

    guardadas = 0
    with get_connection() as conn:
        for fila, vector in zip(filas, vectores):
            try:
                guardar_tramite_completo(conn, fila, vector)
                marcar_activos(conn, [fila["id"]])
                conn.commit()
                guardadas += 1
            except Exception:
                conn.rollback()
                logger.exception("trámite %s falló al guardar, se salta", fila["id"])
        if bajas:
            marcar_inactivos(conn, bajas)
        guardar_sync_state(conn)
        conn.commit()
    logger.info("sync ok: %d/%d guardadas, %d bajas", guardadas, len(filas), len(bajas))


if __name__ == "__main__":
    main()
