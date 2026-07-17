"""Eval comparativo offline: NO toca la DB (las dims difieren: bge-m3 1024 vs e5-base 768).

Uso:
    venv/Scripts/python.exe tests/eval_comparativo.py --embeddings           # nvidia vs st, hit@k + gate
    venv/Scripts/python.exe tests/eval_comparativo.py --sintesis 5          # NIM vs Ollama lado a lado

Requiere: pip install -r requirements-oss.txt; para --sintesis, Ollama corriendo con el modelo bajado.
"""
import argparse
import sys

import numpy as np
from dotenv import load_dotenv

sys.path.insert(0, ".")

from api.confidence import evaluar_confianza
from api.prompts import system_de_sintesis
from db.connection import get_connection
from db.queries import buscar_tramites
from ingest.mapper import texto_para_embedding
from providers import factory
from providers.openai_compat import OpenAICompatChatProvider
from providers.st_embeddings import SentenceTransformersEmbeddingProvider
from tests.eval_dataset import CASOS, normalizar


def cargar_corpus() -> list[dict]:
    with get_connection() as conn:
        filas = conn.execute(
            "SELECT id, nombre, descripcion, sinonimos FROM tramites WHERE activo AND embedding IS NOT NULL"
        ).fetchall()
    return [{"id": f[0], "nombre": f[1], "descripcion": f[2], "sinonimos": f[3]} for f in filas]


def evaluar_backend(nombre: str, embedder, corpus: list[dict]) -> None:
    textos = [texto_para_embedding(c) for c in corpus]
    print(f"[{nombre}] embebiendo corpus ({len(textos)} docs)...")
    docs = np.array(embedder.embed_documents(textos), dtype=np.float32)
    docs /= np.linalg.norm(docs, axis=1, keepdims=True)

    frases = [c["frase"] for c in CASOS]
    print(f"[{nombre}] embebiendo {len(frases)} consultas...")
    consultas = np.array([embedder.embed_query(f) for f in frases], dtype=np.float32)
    consultas /= np.linalg.norm(consultas, axis=1, keepdims=True)

    distancias = 1.0 - consultas @ docs.T  # distancia coseno
    hit1 = hit5 = directas = 0
    aclara_ok = ambiguas = gateadas = negativas = claro_mal = 0
    for i, caso in enumerate(CASOS):
        top5 = np.argsort(distancias[i])[:5]
        d5 = [float(distancias[i][j]) for j in top5]
        v = evaluar_confianza(d5)
        nombres = [normalizar(corpus[j]["nombre"]) for j in top5]
        if caso["clase"] == "directa":
            directas += 1
            esperado = normalizar(caso["esperado"])
            en1 = esperado in nombres[0]
            hit1 += en1
            hit5 += any(esperado in n for n in nombres)
            if v == "claro" and not en1:
                claro_mal += 1
        elif caso["clase"] == "ambigua":
            ambiguas += 1
            aclara_ok += v == "ambiguo"
        else:
            negativas += 1
            gateadas += v in ("lejano", "vacio")
            claro_mal += v == "claro"
    print(f"[{nombre}] hit@1 {hit1}/{directas}  hit@5 {hit5}/{directas}  "
          f"aclara {aclara_ok}/{ambiguas}  gateadas {gateadas}/{negativas}  claro_mal {claro_mal}")
    print(f"[{nombre}] nota: el gate usa umbrales calibrados para bge-m3; para {nombre} son solo indicativos\n")


def comparar_sintesis(n: int) -> None:
    import os

    os.makedirs("docs/evals", exist_ok=True)
    nim = factory.chat_potente()  # PROVIDER default (nvidia)
    ollama = OpenAICompatChatProvider(
        model=os.environ.get("MODELO_POTENTE_OLLAMA", "llama3.1:8b"),
        base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        api_key="ollama",
    )
    emb = factory.embedder()
    directas = [c for c in CASOS if c["clase"] == "directa"][:n]
    lineas = ["# Síntesis comparada NIM vs Ollama\n"]
    with get_connection() as conn:
        for caso in directas:
            hits = buscar_tramites(conn, emb.embed_query(caso["frase"]), limit=1)
            if not hits:
                continue
            system = system_de_sintesis(hits[0])
            mensajes = [{"role": "user", "content": caso["frase"]}]
            lineas.append(f"\n## {caso['frase']}\n")
            lineas.append(f"**Trámite:** {hits[0]['nombre']}\n")
            lineas.append("### NIM\n\n" + nim.complete(system=system, messages=mensajes, max_tokens=800) + "\n")
            lineas.append("### Ollama\n\n" + ollama.complete(system=system, messages=mensajes, max_tokens=800) + "\n")
            print(f"ok: {caso['frase']}")
    with open("docs/evals/sintesis-comparada.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lineas))
    print("escrito docs/evals/sintesis-comparada.md")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--embeddings", action="store_true")
    parser.add_argument("--sintesis", type=int, default=0)
    args = parser.parse_args()

    if args.embeddings:
        corpus = cargar_corpus()
        evaluar_backend("nvidia/bge-m3", factory.embedder(), corpus)
        evaluar_backend("st/multilingual-e5-base", SentenceTransformersEmbeddingProvider(), corpus)
    if args.sintesis:
        comparar_sintesis(args.sintesis)


if __name__ == "__main__":
    main()
