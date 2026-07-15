"""Genera variantes coloquiales de las frases directas con el modelo potente.
Uso: venv/Scripts/python.exe tests/generar_variantes.py
Imprime literales Python para revisar A MANO y pegar en eval_dataset.py (curación obligatoria:
eliminar las que cambian el sentido o repiten una frase existente)."""
import sys

from dotenv import load_dotenv

sys.path.insert(0, ".")

from providers import factory
from tests.eval_dataset import CASOS

SISTEMA = """Sos un ciudadano boliviano común escribiendo a un chat de trámites del Estado.
Reformulá la frase dada en 2 variantes coloquiales distintas, como hablaría gente real
(informal, a veces con contexto personal, sin tecnicismos). Español de Bolivia.
Respondé SOLO las 2 variantes, una por línea, sin numeración ni comillas."""


def main() -> None:
    load_dotenv()
    chat = factory.chat_potente()
    for caso in [c for c in CASOS if c["clase"] == "directa"]:
        texto = chat.complete(
            system=SISTEMA,
            messages=[{"role": "user", "content": caso["frase"]}],
            max_tokens=200,
        )
        for linea in texto.strip().splitlines():
            frase = linea.strip().strip('"')
            if frase:
                print(f'    {{"frase": {frase!r}, "clase": "directa", "esperado": {caso["esperado"]!r}}},')


if __name__ == "__main__":
    main()
