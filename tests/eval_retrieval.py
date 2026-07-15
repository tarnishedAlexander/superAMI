"""Eval manual de retrieval con frases coloquiales reales.

Uso: python tests/eval_retrieval.py
NO es un test de pytest: los MISS son información para calibrar, no fallas.
"""
import sys

from dotenv import load_dotenv

sys.path.insert(0, ".")

from api.confidence import evaluar_confianza
from db.connection import get_connection
from db.queries import buscar_tramites
from providers import factory
from tests.eval_dataset import CASOS, normalizar


def main() -> None:
    load_dotenv()
    emb = factory.embedder()
    hit1 = hit5 = directas = 0
    print(f"{'clase':16} {'frase':40} {'top-1':40} {'d1':>6} {'gap':>6} {'gate':8} resultado")
    with get_connection() as conn:
        for caso in CASOS:
            hits = buscar_tramites(conn, emb.embed_query(caso["frase"]), limit=5)
            distancias = [h["distancia"] for h in hits]
            veredicto = evaluar_confianza(distancias)
            estado = ""
            if caso["clase"] == "directa":
                directas += 1
                nombres = [normalizar(h["nombre"]) for h in hits]
                en1 = bool(nombres) and normalizar(caso["esperado"]) in nombres[0]
                en5 = any(normalizar(caso["esperado"]) in n for n in nombres)
                hit1 += en1
                hit5 += en5
                estado = "HIT@1" if en1 else ("HIT@5" if en5 else "MISS")
            d1 = distancias[0] if distancias else float("nan")
            gap = (distancias[1] - distancias[0]) if len(distancias) > 1 else float("nan")
            top = hits[0]["nombre"] if hits else "-"
            print(f"{caso['clase']:16} {caso['frase']:40.40} {top:40.40} {d1:6.3f} {gap:6.3f} {veredicto:8} {estado}")
    print(f"\ndirectas: hit@1 {hit1}/{directas}   hit@5 {hit5}/{directas}")
    print("Para el barrido de umbrales usar tests/calibrar_gate.py")


if __name__ == "__main__":
    main()
