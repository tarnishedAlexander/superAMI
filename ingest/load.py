"""Carga única del dataset tramites-bo a Postgres.

Uso:
    python -m ingest.load                      # descarga y carga todo
    python -m ingest.load --limit 30 --skip-llm --skip-embeddings   # prueba rápida
"""
import argparse
import json
import logging
import urllib.request

from dotenv import load_dotenv

from db.connection import get_connection
from db.queries import guardar_tramite_completo
from ingest.costo_llm import extraer_costo
from ingest.mapper import mapear_tramite, texto_para_embedding
from providers import factory

URL_TRAMITES = "https://raw.githubusercontent.com/datosbolivia/tramites-bo/main/tramites.jsonl"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def leer_registros(ruta: str | None) -> list[dict]:
    if ruta:
        with open(ruta, encoding="utf-8") as f:
            lineas = f.readlines()
    else:
        logger.info("descargando %s", URL_TRAMITES)
        with urllib.request.urlopen(URL_TRAMITES) as respuesta:
            lineas = respuesta.read().decode("utf-8").splitlines()
    return [json.loads(linea) for linea in lineas if linea.strip()]


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--jsonl", default=None, help="ruta local a tramites.jsonl (default: descarga)")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip-llm", action="store_true")
    parser.add_argument("--skip-embeddings", action="store_true")
    args = parser.parse_args()

    registros = leer_registros(args.jsonl)
    if args.limit:
        registros = registros[: args.limit]

    filas = []
    for registro in registros:
        try:
            filas.append(mapear_tramite(registro))
        except Exception:
            logger.exception("registro %s falló en el mapeo, se salta", registro.get("id"))
    logger.info("mapeadas %d filas (%d necesitan LLM para costo)", len(filas), sum(f["necesita_llm"] for f in filas))

    if not args.skip_llm:
        chat = factory.chat_potente()
        for fila in filas:
            if not fila["necesita_llm"]:
                continue
            datos = extraer_costo(chat, fila["descripcion"], fila["resultado"])
            if datos:
                fila.update(
                    costo_monto=datos["monto"], costo_moneda=datos["moneda"], costo_concepto=datos["concepto"]
                )
                logger.info("costo extraído para %s: %s %s", fila["id"], datos["monto"], datos["moneda"])
            else:
                logger.info("sin costo extraíble para %s", fila["id"])

    vectores: list[list[float] | None] = [None] * len(filas)
    if not args.skip_embeddings:
        emb = factory.embedder()
        logger.info("generando %d embeddings...", len(filas))
        vectores = emb.embed_documents([texto_para_embedding(f) for f in filas])

    guardadas = 0
    with get_connection() as conn:
        for fila, vector in zip(filas, vectores):
            try:
                guardar_tramite_completo(conn, fila, vector)
                conn.commit()
                guardadas += 1
            except Exception:
                conn.rollback()
                logger.exception("trámite %s falló al guardar, se salta", fila["id"])
    logger.info("guardadas %d/%d filas", guardadas, len(filas))


if __name__ == "__main__":
    main()
