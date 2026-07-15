"""Chequea que cada `esperado` de las directas exista en algún nombre de trámite de la DB.
Uso: venv/Scripts/python.exe tests/verificar_eval.py
Las directas cuyo esperado no exista deben reclasificarse (no_satisfacible) o eliminarse."""
import sys

from dotenv import load_dotenv

sys.path.insert(0, ".")

from db.connection import get_connection
from tests.eval_dataset import CASOS, normalizar


def main() -> None:
    load_dotenv()
    with get_connection() as conn:
        nombres = [normalizar(f[0]) for f in conn.execute("SELECT nombre FROM tramites").fetchall()]
    directas = [c for c in CASOS if c["clase"] == "directa"]
    faltantes = [
        c for c in directas if not any(normalizar(c["esperado"]) in n for n in nombres)
    ]
    print(f"directas: {len(directas)} | esperados sin trámite en la DB: {len(faltantes)}")
    for c in faltantes:
        print(f"  RECLASIFICAR/ELIMINAR: {c['frase']!r} (esperado {c['esperado']!r})")


if __name__ == "__main__":
    main()
