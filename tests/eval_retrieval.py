"""Eval manual de retrieval con frases coloquiales reales.

Uso: python tests/eval_retrieval.py
NO es un test de pytest: los MISS son información para calibrar, no fallas.
"""
import sys
import unicodedata

from dotenv import load_dotenv

sys.path.insert(0, ".")

from db.connection import get_connection
from db.queries import buscar_tramites
from providers import factory

# (frase coloquial, substring esperado en el nombre de algún trámite del top-5)
# Verificado contra el dataset 2026-07-14: los casos marcados [SIN DATO] no tienen
# trámite correspondiente en gob.bo (cédula/SEGIP, licencias/Policía, pasaporte/
# Migración, bonos y soltería no publican ahí) — su MISS mide cobertura del
# dataset, no calidad del retrieval; sirven como control negativo del gate.
CASOS = [
    ("quiero sacar mi carnet", "CEDULA"),  # [SIN DATO]
    ("renovar mi carnet de identidad", "CEDULA"),  # [SIN DATO]
    ("necesito el papel del carro", "VEHICULO"),
    ("certificado de nacimiento", "NACIMIENTO"),
    ("quiero casarme, qué necesito", "MATRIMONIO"),  # [SIN DATO]
    ("sacar el NIT para mi negocio", "NIT"),
    ("licencia de conducir por primera vez", "LICENCIA"),  # [SIN DATO]
    ("sacar pasaporte", "PASAPORTE"),  # [SIN DATO]
    ("certificado de antecedentes penales", "ANTECEDENTES"),
    ("quiero abrir mi empresa", "EMPRESA"),
    ("bono Juana Azurduy", "JUANA AZURDUY"),  # [SIN DATO]
    ("cobrar la renta dignidad", "RENTA DIGNIDAD"),
    ("certificado de soltería", "SOLTER"),  # [SIN DATO]
    ("quiero poner una farmacia", "FARMACIA"),
    ("carnet de discapacidad", "DISCAPACIDAD"),
    ("título de bachiller", "BACHILLER"),
    ("registrar a mi hijo recién nacido", "NACIMIENTO"),
    ("apostillar mis documentos para salir del país", "APOSTILLA"),
    ("certificado de defunción", "DEFUNCION"),
]


def _normalizar(texto: str) -> str:
    sin_acentos = unicodedata.normalize("NFD", texto)
    return "".join(c for c in sin_acentos if unicodedata.category(c) != "Mn").upper()


def main() -> None:
    load_dotenv()
    emb = factory.embedder()
    hit1 = hit5 = 0
    print(f"{'frase':45} {'top-1':45} {'d1':>6} {'d2':>6} {'gap':>6} resultado")
    with get_connection() as conn:
        for frase, esperado in CASOS:
            hits = buscar_tramites(conn, emb.embed_query(frase), limit=5)
            nombres = [_normalizar(h["nombre"]) for h in hits]
            en1 = bool(nombres) and _normalizar(esperado) in nombres[0]
            en5 = any(_normalizar(esperado) in n for n in nombres)
            hit1 += en1
            hit5 += en5
            d1 = hits[0]["distancia"] if hits else float("nan")
            d2 = hits[1]["distancia"] if len(hits) > 1 else float("nan")
            estado = "HIT@1" if en1 else ("HIT@5" if en5 else "MISS")
            print(f"{frase:45.45} {hits[0]['nombre'] if hits else '-':45.45} {d1:6.3f} {d2:6.3f} {d2 - d1:6.3f} {estado}")
            if not en5:
                for h in hits[1:4]:
                    print(f"{'':45} > {h['nombre'][:70]}")
    print(f"\nhit@1: {hit1}/{len(CASOS)}   hit@5: {hit5}/{len(CASOS)}")
    print("Calibración: elegir UMBRAL_GAP ~ mediana de gaps de los HIT@1, y UMBRAL_DISTANCIA_MAX ~ máx d1 de los HIT.")


if __name__ == "__main__":
    main()
