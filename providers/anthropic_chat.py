import json
import logging
from typing import Iterator

import anthropic

logger = logging.getLogger(__name__)


class AnthropicChatProvider:
    """ChatProvider sobre la Messages API de Anthropic.

    Sin temperature/top_p/top_k: Sonnet 5 rechaza valores no default.
    """

    def __init__(self, model: str, client: anthropic.Anthropic | None = None):
        self.model = model
        self._client = client or anthropic.Anthropic()

    def complete(self, *, system: str, messages: list[dict], max_tokens: int = 1024) -> str:
        respuesta = self._client.messages.create(
            model=self.model, system=system, messages=messages, max_tokens=max_tokens
        )
        return "".join(b.text for b in respuesta.content if b.type == "text")

    def complete_json(self, *, system: str, messages: list[dict], schema: dict, max_tokens: int = 1024) -> dict | None:
        try:
            respuesta = self._client.messages.create(
                model=self.model,
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
            texto = next((b.text for b in respuesta.content if b.type == "text"), None)
            return json.loads(texto) if texto else None
        except (anthropic.APIError, json.JSONDecodeError, StopIteration):
            logger.warning("complete_json falló para %s", self.model, exc_info=True)
            return None

    def stream(self, *, system: str, messages: list[dict], max_tokens: int = 4096) -> Iterator[str]:
        with self._client.messages.stream(
            model=self.model, system=system, messages=messages, max_tokens=max_tokens
        ) as s:
            yield from s.text_stream
