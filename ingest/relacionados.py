"""Clasifica relaciones procedimentales entre trámites con el modelo potente.

Uso:
    python -m ingest.relacionados --muestra 20   # imprime 20 para validar A MANO, no escribe
    python -m ingest.relacionados                # corre todo y persiste (~30-45 min por rate limit)
    python -m ingest.relacionados --desde 5000   # retoma desde un id (corridas interrumpidas)
"""
import argparse
import logging
import time

from dotenv import load_dotenv

from api.prompts import SISTEMA_RELACIONES, schema_relaciones, usuario_relaciones
from db.connection import get_connection
from db.queries import candidatos_relacionados, guardar_relaciones
from providers import factory

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PAUSA_SEGUNDOS = 1.6  # free tier NIM ~40 req/min
TIPOS_VALIDOS = ("siguiente_paso", "requisito_previo", "alternativa", "mismo_evento", "ninguna")


def _normalizar_relaciones(datos, ids_validos: list[int]) -> list[dict]:
    """Tolera dos formas de respuesta del modelo:
    - la pedida por schema_relaciones: {"relaciones": [{"id", "tipo"}, ...]}
    - un objeto plano {"<id>": "<tipo>", ...}: NIM a veces aplana schemas anidados
      con guided_json (hallazgo de la validación manual de la Task 13, 2026-07-15).
    """
    if not isinstance(datos, dict):
        return []
    if isinstance(datos.get("relaciones"), list):
        return datos["relaciones"]
    relaciones = []
    for clave, valor in datos.items():
        try:
            id_ = int(clave)
        except (TypeError, ValueError):
            continue
        relaciones.append({"id": id_, "tipo": valor})
    return relaciones


def clasificar_tramite(chat, base: dict, candidatos: list[dict]) -> list[dict]:
    """Fail-open: lista vacía si el modelo no coopera."""
    if not candidatos:
        return []
    ids_validos = [c["id"] for c in candidatos]
    datos = chat.complete_json(
        system=SISTEMA_RELACIONES,
        messages=[{"role": "user", "content": usuario_relaciones(base, candidatos)}],
        schema=schema_relaciones(ids_validos),
        max_tokens=500,
    )
    if not datos:
        return []
    relaciones = _normalizar_relaciones(datos, ids_validos)
    return [
        r for r in relaciones
        if isinstance(r, dict) and r.get("id") in ids_validos and r.get("tipo") in TIPOS_VALIDOS
    ]


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--muestra", type=int, default=None, help="clasificar N trámites e imprimir, sin escribir")
    parser.add_argument("--desde", type=int, default=0, help="retomar desde este tramite_id")
    args = parser.parse_args()

    chat = factory.chat_potente()
    with get_connection() as conn:
        bases = conn.execute(
            "SELECT id, nombre, descripcion FROM tramites "
            "WHERE activo AND embedding IS NOT NULL AND id >= %s ORDER BY id",
            (args.desde,),
        ).fetchall()
    bases = [{"id": f[0], "nombre": f[1], "descripcion": f[2]} for f in bases]
    if args.muestra:
        bases = bases[: args.muestra]

    procesados = con_relacion = 0
    with get_connection() as conn:
        for base in bases:
            candidatos = candidatos_relacionados(conn, base["id"], limit=5)
            relaciones = clasificar_tramite(chat, base, candidatos)
            utiles = [r for r in relaciones if r["tipo"] != "ninguna"]
            if args.muestra:
                nombres = {c["id"]: c["nombre"] for c in candidatos}
                print(f"\nBASE [{base['id']}] {base['nombre']}")
                for r in relaciones:
                    print(f"  {r['tipo']:16} -> [{r['id']}] {nombres.get(r['id'], '?')}")
            else:
                try:
                    guardar_relaciones(conn, base["id"], relaciones)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    logger.exception("fallo guardando relaciones de %s", base["id"])
            procesados += 1
            con_relacion += bool(utiles)
            if procesados % 50 == 0:
                logger.info("procesados %d/%d (%d con alguna relación)", procesados, len(bases), con_relacion)
            time.sleep(PAUSA_SEGUNDOS)
    logger.info("listo: %d procesados, %d con alguna relación útil", procesados, con_relacion)


if __name__ == "__main__":
    main()
