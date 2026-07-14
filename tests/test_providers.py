import json
from types import SimpleNamespace

from providers.anthropic_chat import AnthropicChatProvider
from providers.openai_compat import OpenAICompatChatProvider, OpenAICompatEmbeddingProvider

# ---------- fakes del SDK de Anthropic ----------


class FakeBlock:
    def __init__(self, type_, text=""):
        self.type = type_
        self.text = text


class FakeResponse:
    def __init__(self, blocks):
        self.content = blocks


class FakeMessages:
    def __init__(self, response):
        self._response = response
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._response


class FakeClient:
    def __init__(self, response):
        self.messages = FakeMessages(response)


def test_anthropic_complete_concatena_bloques_de_texto():
    client = FakeClient(FakeResponse([FakeBlock("thinking"), FakeBlock("text", "Hola "), FakeBlock("text", "mundo")]))
    provider = AnthropicChatProvider(model="claude-haiku-4-5", client=client)
    resultado = provider.complete(system="sos un test", messages=[{"role": "user", "content": "hola"}])
    assert resultado == "Hola mundo"
    assert client.messages.last_kwargs["model"] == "claude-haiku-4-5"
    assert "temperature" not in client.messages.last_kwargs


def test_anthropic_complete_json_parsea_y_pasa_schema():
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"], "additionalProperties": False}
    client = FakeClient(FakeResponse([FakeBlock("text", '{"a": 1}')]))
    provider = AnthropicChatProvider(model="claude-haiku-4-5", client=client)
    datos = provider.complete_json(system="s", messages=[{"role": "user", "content": "m"}], schema=schema)
    assert datos == {"a": 1}
    assert client.messages.last_kwargs["output_config"] == {"format": {"type": "json_schema", "schema": schema}}


def test_anthropic_complete_json_devuelve_none_con_json_invalido():
    client = FakeClient(FakeResponse([FakeBlock("text", "esto no es json")]))
    provider = AnthropicChatProvider(model="claude-haiku-4-5", client=client)
    assert provider.complete_json(system="s", messages=[{"role": "user", "content": "m"}], schema={}) is None


def test_anthropic_complete_json_nunca_lanza():
    class ClienteQueExplota:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise TypeError("shape inesperado")

    provider = AnthropicChatProvider(model="claude-haiku-4-5", client=ClienteQueExplota())
    assert provider.complete_json(system="s", messages=[{"role": "user", "content": "m"}], schema={}) is None


# ---------- fakes de cliente OpenAI-compatible (NVIDIA NIM) ----------


class FakeCompletions:
    def __init__(self, respuestas):
        self._respuestas = list(respuestas)
        self.llamadas = []

    def create(self, **kwargs):
        self.llamadas.append(kwargs)
        resultado = self._respuestas.pop(0)
        if isinstance(resultado, Exception):
            raise resultado
        return resultado


def _resp_chat(texto):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=texto))])


def _resp_embed(*vectores):
    return SimpleNamespace(data=[SimpleNamespace(embedding=list(v)) for v in vectores])


def _fake_openai(chat=(), embed=()):
    return SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions(chat)),
        embeddings=FakeCompletions(embed),
    )


def test_openai_complete_prepende_system():
    cliente = _fake_openai(chat=[_resp_chat("hola!")])
    provider = OpenAICompatChatProvider(model="meta/llama-3.1-8b-instruct", base_url="http://x", api_key="k", client=cliente)
    assert provider.complete(system="sos un test", messages=[{"role": "user", "content": "hola"}]) == "hola!"
    llamada = cliente.chat.completions.llamadas[0]
    assert llamada["messages"][0] == {"role": "system", "content": "sos un test"}
    assert llamada["model"] == "meta/llama-3.1-8b-instruct"
    assert "temperature" not in llamada


def test_openai_complete_json_usa_guided_json():
    cliente = _fake_openai(chat=[_resp_chat('{"a": 1}')])
    provider = OpenAICompatChatProvider(model="m", base_url="http://x", api_key="k", client=cliente)
    datos = provider.complete_json(system="s", messages=[{"role": "user", "content": "m"}], schema={"type": "object"})
    assert datos == {"a": 1}
    assert cliente.chat.completions.llamadas[0]["extra_body"] == {"nvext": {"guided_json": {"type": "object"}}}


def test_openai_complete_json_fallback_sin_guided():
    cliente = _fake_openai(chat=[RuntimeError("guided no soportado"), _resp_chat('bla {"a": 2} bla')])
    provider = OpenAICompatChatProvider(model="m", base_url="http://x", api_key="k", client=cliente)
    datos = provider.complete_json(system="s", messages=[{"role": "user", "content": "m"}], schema={"type": "object"})
    assert datos == {"a": 2}
    assert "extra_body" not in cliente.chat.completions.llamadas[1]


def test_openai_complete_json_fallback_cuando_guided_devuelve_prosa():
    # NIM puede ignorar guided_json silenciosamente: 200 OK con prosa en vez de JSON
    cliente = _fake_openai(chat=[_resp_chat("monto: 50, moneda: Bs"), _resp_chat('{"a": 3}')])
    provider = OpenAICompatChatProvider(model="m", base_url="http://x", api_key="k", client=cliente)
    datos = provider.complete_json(system="s", messages=[{"role": "user", "content": "m"}], schema={"type": "object"})
    assert datos == {"a": 3}
    llamadas = cliente.chat.completions.llamadas
    assert "extra_body" not in llamadas[1]
    assert "JSON" in llamadas[1]["messages"][0]["content"]


def test_openai_complete_json_nunca_lanza():
    cliente = _fake_openai(chat=[RuntimeError("boom"), RuntimeError("boom")])
    provider = OpenAICompatChatProvider(model="m", base_url="http://x", api_key="k", client=cliente)
    assert provider.complete_json(system="s", messages=[{"role": "user", "content": "m"}], schema={}) is None


def test_openai_embeddings_input_type_y_lotes():
    cliente = _fake_openai(embed=[_resp_embed([0.1, 0.2]), _resp_embed([0.3, 0.4])])
    provider = OpenAICompatEmbeddingProvider(model="baai/bge-m3", base_url="http://x", api_key="k", client=cliente)
    provider._TAMANO_LOTE = 1  # fuerza dos lotes
    vectores = provider.embed_documents(["a", "b"])
    assert vectores == [[0.1, 0.2], [0.3, 0.4]]
    assert [l["extra_body"]["input_type"] for l in cliente.embeddings.llamadas] == ["passage", "passage"]


def test_openai_embed_query_usa_input_type_query():
    cliente = _fake_openai(embed=[_resp_embed([0.5, 0.6])])
    provider = OpenAICompatEmbeddingProvider(model="baai/bge-m3", base_url="http://x", api_key="k", client=cliente)
    assert provider.embed_query("carnet") == [0.5, 0.6]
    assert cliente.embeddings.llamadas[0]["extra_body"]["input_type"] == "query"
