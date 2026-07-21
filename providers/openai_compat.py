import json
import logging
import os
import re
import time
from typing import Iterator

from openai import APIError, OpenAI

logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _objetos_balanceados(texto: str):
    """Yield de subcadenas {...} con llaves balanceadas (ignora las de strings)."""
    profundidad = inicio = 0
    en_cadena = escapado = False
    for i, ch in enumerate(texto):
        if en_cadena:
            if escapado:
                escapado = False
            elif ch == "\\":
                escapado = True
            elif ch == '"':
                en_cadena = False
            continue
        if ch == '"':
            en_cadena = True
        elif ch == "{":
            if profundidad == 0:
                inicio = i
            profundidad += 1
        elif ch == "}" and profundidad > 0:
            profundidad -= 1
            if profundidad == 0:
                yield texto[inicio : i + 1]


def _extraer_json(texto: str) -> dict | None:
    """Extrae un objeto JSON tolerando modelos que razonan (nemotron/deepseek):
    quita trazas <think>, ignora prosa alrededor y, si hay varios objetos, se
    queda con el más rico (el que más claves tiene = la ficha, no un {} suelto)."""
    if not texto:
        return None
    texto = _THINK_RE.sub("", texto).replace("<think>", "").replace("</think>", "")
    # camino rápido: primer '{' a último '}' (cubre el caso de salida JSON limpia)
    match = _JSON_RE.search(texto)
    if match:
        try:
            datos = json.loads(match.group(0))
            if isinstance(datos, dict):
                return datos
        except json.JSONDecodeError:
            pass
    # fallback robusto: escanear objetos balanceados y quedarse con el más rico
    mejor: dict | None = None
    for frag in _objetos_balanceados(texto):
        try:
            datos = json.loads(frag)
        except json.JSONDecodeError:
            continue
        if isinstance(datos, dict) and (mejor is None or len(datos) > len(mejor)):
            mejor = datos
    return mejor


class OpenAICompatChatProvider:
    """ChatProvider sobre cualquier API OpenAI-compatible (NVIDIA NIM, Ollama, vLLM...).

    Sin parámetros de sampling: se usan los defaults del servidor.
    """

    def __init__(self, model: str, base_url: str, api_key: str, client: OpenAI | None = None):
        self.model = model
        # timeout explícito: sin él, un request colgado del free tier de NIM
        # bloquea hasta 600s por intento (corridas batch de horas se vuelven días)
        self._client = client or OpenAI(base_url=base_url, api_key=api_key, timeout=60.0, max_retries=3)

    def _mensajes(self, system: str, messages: list[dict]) -> list[dict]:
        return [{"role": "system", "content": system}, *messages]

    def complete(self, *, system: str, messages: list[dict], max_tokens: int = 1024) -> str:
        for intento in range(3):
            try:
                respuesta = self._client.chat.completions.create(
                    model=self.model, messages=self._mensajes(system, messages), max_tokens=max_tokens
                )
                return respuesta.choices[0].message.content or ""
            except Exception as error:
                if intento == 2:
                    raise
                espera = 5 * (intento + 1)
                logger.warning("complete falló (%s), reintento en %ss", error, espera)
                time.sleep(espera)

    def complete_json(self, *, system: str, messages: list[dict], schema: dict, max_tokens: int = 1024) -> dict | None:
        """Fail-open: nunca lanza; devuelve None ante cualquier problema."""
        for intento in range(3):
            try:
                datos = None
                # SALTAR_GUIDED_JSON=1: para modelos que ignoran nvext.guided_json
                # (p.ej. deepseek-v4-flash) el primer intento siempre falla y duplica
                # el costo de cada llamada — ir directo al fallback de prompt
                if os.environ.get("SALTAR_GUIDED_JSON") != "1":
                    try:
                        # guided decoding de NVIDIA NIM (vLLM); no todos los modelos lo soportan
                        respuesta = self._client.chat.completions.create(
                            model=self.model,
                            messages=self._mensajes(system, messages),
                            max_tokens=max_tokens,
                            extra_body={"nvext": {"guided_json": schema}},
                        )
                        datos = _extraer_json(respuesta.choices[0].message.content or "")
                    except Exception:
                        pass
                if datos is None:
                    # algunos endpoints ignoran guided_json sin error y devuelven prosa
                    respuesta = self._client.chat.completions.create(
                        model=self.model,
                        messages=self._mensajes(
                            system + "\nRespondé ÚNICAMENTE con un objeto JSON válido, sin texto adicional.",
                            messages,
                        ),
                        max_tokens=max_tokens,
                    )
                    datos = _extraer_json(respuesta.choices[0].message.content or "")
                return datos
            except Exception as error:
                if intento == 2:
                    logger.warning("complete_json falló para %s", self.model, exc_info=True)
                    return None
                espera = 5 * (intento + 1)
                logger.warning("complete_json falló (%s), reintento en %ss", error, espera)
                time.sleep(espera)

    def stream(self, *, system: str, messages: list[dict], max_tokens: int = 4096) -> Iterator[str]:
        for intento in range(3):
            try:
                chunks = self._client.chat.completions.create(
                    model=self.model,
                    messages=self._mensajes(system, messages),
                    max_tokens=max_tokens,
                    stream=True,
                )
                break
            except Exception as error:
                if intento == 2:
                    raise
                espera = 5 * (intento + 1)
                logger.warning("chat stream falló (%s), reintento en %ss", error, espera)
                time.sleep(espera)

        last_chunk = ""
        for chunk in chunks:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                if last_chunk and delta.startswith(last_chunk) and len(delta) > len(last_chunk):
                    nuevo = delta[len(last_chunk):]
                    last_chunk = delta
                    yield nuevo
                else:
                    last_chunk = delta
                    yield delta


class OpenAICompatEmbeddingProvider:
    """EmbeddingProvider sobre /v1/embeddings OpenAI-compatible (modelos retrieval de NVIDIA NIM)."""

    _TAMANO_LOTE = 32

    def __init__(self, model: str, base_url: str, api_key: str, client: OpenAI | None = None):
        self.model = model
        # timeout explícito: sin él, un request colgado del free tier de NIM
        # bloquea hasta 600s por intento (corridas batch de horas se vuelven días)
        self._client = client or OpenAI(base_url=base_url, api_key=api_key, timeout=60.0, max_retries=3)

    def _embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        respuesta = self._client.embeddings.create(
            model=self.model,
            input=texts,
            extra_body={"input_type": input_type, "truncate": "END"},
        )
        return [d.embedding for d in respuesta.data]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectores: list[list[float]] = []
        for i in range(0, len(texts), self._TAMANO_LOTE):
            lote = texts[i : i + self._TAMANO_LOTE]
            for intento in range(3):
                try:
                    vectores.extend(self._embed(lote, "passage"))
                    break
                except APIError as error:
                    if intento == 2:
                        raise
                    espera = 15 * (intento + 1)
                    logger.warning("embeddings falló (%s), reintento en %ss", error, espera)
                    time.sleep(espera)
        return vectores

    def embed_query(self, text: str) -> list[float]:
        for intento in range(3):
            try:
                return self._embed([text], "query")[0]
            except Exception as error:
                if intento == 2:
                    raise
                espera = 5 * (intento + 1)
                logger.warning("embed_query falló (%s), reintento en %ss", error, espera)
                time.sleep(espera)

