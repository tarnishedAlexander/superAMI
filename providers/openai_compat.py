import json
import logging
import re
import time
from typing import Iterator

from openai import APIError, OpenAI

logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extraer_json(texto: str) -> dict | None:
    match = _JSON_RE.search(texto or "")
    if not match:
        return None
    try:
        datos = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return datos if isinstance(datos, dict) else None


class OpenAICompatChatProvider:
    """ChatProvider sobre cualquier API OpenAI-compatible (NVIDIA NIM, Ollama, vLLM...).

    Sin parámetros de sampling: se usan los defaults del servidor.
    """

    def __init__(self, model: str, base_url: str, api_key: str, client: OpenAI | None = None):
        self.model = model
        self._client = client or OpenAI(base_url=base_url, api_key=api_key)

    def _mensajes(self, system: str, messages: list[dict]) -> list[dict]:
        return [{"role": "system", "content": system}, *messages]

    def complete(self, *, system: str, messages: list[dict], max_tokens: int = 1024) -> str:
        respuesta = self._client.chat.completions.create(
            model=self.model, messages=self._mensajes(system, messages), max_tokens=max_tokens
        )
        return respuesta.choices[0].message.content or ""

    def complete_json(self, *, system: str, messages: list[dict], schema: dict, max_tokens: int = 1024) -> dict | None:
        """Fail-open: nunca lanza; devuelve None ante cualquier problema."""
        try:
            datos = None
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
        except Exception:
            logger.warning("complete_json falló para %s", self.model, exc_info=True)
            return None

    def stream(self, *, system: str, messages: list[dict], max_tokens: int = 4096) -> Iterator[str]:
        chunks = self._client.chat.completions.create(
            model=self.model,
            messages=self._mensajes(system, messages),
            max_tokens=max_tokens,
            stream=True,
        )
        for chunk in chunks:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta


class OpenAICompatEmbeddingProvider:
    """EmbeddingProvider sobre /v1/embeddings OpenAI-compatible (modelos retrieval de NVIDIA NIM)."""

    _TAMANO_LOTE = 32

    def __init__(self, model: str, base_url: str, api_key: str, client: OpenAI | None = None):
        self.model = model
        self._client = client or OpenAI(base_url=base_url, api_key=api_key)

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
        return self._embed([text], "query")[0]
