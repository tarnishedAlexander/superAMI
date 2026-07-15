"""Barrido de umbrales del gate sobre tests/eval_dataset.py.
Uso: venv/Scripts/python.exe tests/calibrar_gate.py
Una sola pasada de retrieval por frase; el barrido es matemática pura.
Criterio de elección: cero "claro incorrecto" primero; después maximizar
one-shot de directas + aclaración de ambiguas + gateo de no satisfacibles."""
import sys

from dotenv import load_dotenv

sys.path.insert(0, ".")

from api.confidence import UMBRAL_DISTANCIA_MAX, UMBRAL_GAP, evaluar_confianza
from db.connection import get_connection
from db.queries import buscar_tramites
from providers import factory
from tests.eval_dataset import CASOS, normalizar

GAPS = [round(0.005 * i, 3) for i in range(1, 21)]          # 0.005 .. 0.100 (gap 0 desactivaría la aclaración)
DISTS = [round(0.40 + 0.01 * i, 2) for i in range(0, 31)]   # 0.40 .. 0.70


def preparar() -> list[tuple[dict, list[float], bool]]:
    emb = factory.embedder()
    filas = []
    with get_connection() as conn:
        for caso in CASOS:
            hits = buscar_tramites(conn, emb.embed_query(caso["frase"]), limit=5)
            distancias = [h["distancia"] for h in hits]
            top1_ok = (
                caso["clase"] == "directa"
                and bool(hits)
                and normalizar(caso["esperado"]) in normalizar(hits[0]["nombre"])
            )
            filas.append((caso, distancias, top1_ok))
    return filas


def medir(filas, umbral_gap: float, umbral_dist: float) -> dict:
    m = {"directas": 0, "one_shot": 0, "claro_incorrecto": 0,
         "ambiguas": 0, "aclara_ok": 0, "negativas": 0, "gateadas": 0}
    for caso, distancias, top1_ok in filas:
        v = evaluar_confianza(distancias, umbral_gap, umbral_dist)
        if caso["clase"] == "directa":
            m["directas"] += 1
            if v == "claro" and top1_ok:
                m["one_shot"] += 1
            if v == "claro" and not top1_ok:
                m["claro_incorrecto"] += 1
        elif caso["clase"] == "ambigua":
            m["ambiguas"] += 1
            if v == "ambiguo":
                m["aclara_ok"] += 1
        else:
            m["negativas"] += 1
            if v in ("lejano", "vacio"):
                m["gateadas"] += 1
            if v == "claro":
                m["claro_incorrecto"] += 1
    return m


def main() -> None:
    load_dotenv()
    filas = preparar()
    resultados = []
    for g in GAPS:
        for d in DISTS:
            m = medir(filas, g, d)
            resultados.append((g, d, m))

    def puntaje(r):
        _, _, m = r
        return (m["claro_incorrecto"] == 0, m["one_shot"] + m["aclara_ok"] + m["gateadas"])

    resultados.sort(key=puntaje, reverse=True)
    print(f"{'gap':>6} {'dist':>6} {'one-shot':>9} {'claro_mal':>9} {'aclara_ok':>9} {'gateadas':>9}")
    for g, d, m in resultados[:15]:
        print(f"{g:6.3f} {d:6.2f} {m['one_shot']:>4}/{m['directas']:<4} {m['claro_incorrecto']:>9} "
              f"{m['aclara_ok']:>4}/{m['ambiguas']:<4} {m['gateadas']:>4}/{m['negativas']:<4}")
    actual = medir(filas, UMBRAL_GAP, UMBRAL_DISTANCIA_MAX)
    print(f"\numbrales actuales gap={UMBRAL_GAP} dist={UMBRAL_DISTANCIA_MAX}: {actual}")
    mejor = resultados[0]
    print(f"recomendado: gap={mejor[0]} dist={mejor[1]}")
    m = mejor[2]
    print(f"criterios spec: one-shot >= 75% de directas -> {m['one_shot']}/{m['directas']}; "
          f"claro incorrecto == 0 -> {m['claro_incorrecto']}; "
          f"negativas gateadas 100% -> {m['gateadas']}/{m['negativas']}")


if __name__ == "__main__":
    main()
