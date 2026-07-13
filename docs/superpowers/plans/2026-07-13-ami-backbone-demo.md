# AMI — Backbone de IA (demo hackathon) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backend RAG funcionando end-to-end: carga del dataset tramites-bo a Postgres+pgvector, retrieval híbrido con gate de confianza, loop de aclaración y respuesta sintetizada en streaming SSE vía `POST /chat`.

**Architecture:** Script de ingesta única (jsonl → mapeo puro → fallback de costo con LLM → embeddings → upsert Postgres). Pipeline online: el modelo económico infiere filtros (JSON), búsqueda vectorial + filtros en pgvector, gate de confianza determinista, aclaración con el modelo económico o síntesis streaming con el modelo potente. Providers detrás de protocolos mínimos — proveedor primario NVIDIA NIM (gratis), Anthropic/Voyage como alternativa por env var.

**Tech Stack:** Python 3.11+, FastAPI + uvicorn, psycopg 3, Postgres 16 + pgvector (Docker), SDK `openai` contra NVIDIA NIM (`integrate.api.nvidia.com/v1`: llama-3.3-70b + llama-3.1-8b + bge-m3 @ 1024 dims), SDKs `anthropic`/`voyageai` como providers alternativos, pytest.

## Global Constraints

- **Proveedor primario: NVIDIA NIM** (keys gratis en build.nvidia.com). Env: `PROVIDER=nvidia` (default), `NVIDIA_API_KEY` (formato `nvapi-...`), `NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1`. Modelos: potente=`meta/llama-3.3-70b-instruct`, económico=`meta/llama-3.1-8b-instruct`, embeddings=`baai/bge-m3` (**1024 dims** → columna `vector(1024)`). Overrideables por `MODELO_POTENTE`, `MODELO_ECONOMICO`, `MODELO_EMBEDDINGS`, `EMBEDDING_DIM`.
- **Proveedor alternativo: Anthropic + Voyage** con `PROVIDER=anthropic` (defaults: `claude-sonnet-5`, `claude-haiku-4-5`, `voyage-4-lite`). Su código vive en el repo aunque el demo corra con NVIDIA. Regla Anthropic: nunca pasar `temperature`/`top_p`/`top_k` (Sonnet 5 rechaza valores no default con 400); structured output vía `output_config={"format": {"type": "json_schema", "schema": ...}}`.
- No setear parámetros de sampling en ningún proveedor (defaults del servidor).
- JSON estructurado en NVIDIA NIM: intentar `extra_body={"nvext": {"guided_json": schema}}` y, si el modelo no lo soporta, caer a instrucción "respondé solo JSON" + parseo tolerante. `complete_json` es fail-open: NUNCA lanza excepción, devuelve `None` ante cualquier fallo. Todo schema de objeto lleva `additionalProperties: false` y `required` con todas las propiedades.
- Texto visible al usuario final: siempre en español. Identificadores de dominio en español (`mapear_tramite`, `buscar_tramites`); infra genérica en inglés (`get_connection`, `ConversationStore`).
- DB: `postgresql://ami:ami@localhost:5433/ami` (puerto 5433 para no chocar con un Postgres local). Embeddings se pasan a SQL como literal string `'[0.1,0.2,...]'::vector` — sin adapter de pgvector en Python.
- Dataset real verificado (2026-07-13): 1,739 registros, todos `estado=PUBLICADO`; `id` viene como string (castear a int); `eventosVida` es lista de **strings**; `palabrasClave` puede ser null; `costos[].costo` siempre parseable a float; fechas `DD/MM/YYYY`.
- Dependencias: solo las de `requirements.txt` del Task 1 más `openai>=1.40` (agregado en Task 2). No agregar otras librerías.
- Git: el repo ya está inicializado (rama `master`). El usuario prefirió no tocar git durante el diseño — **al arrancar la ejecución, confirmar si quiere los commits por task o trabajar sin commits**; si acepta, usar los pasos de commit tal cual.

---

### Task 1: Scaffolding + Postgres con pgvector

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `docker-compose.yml`
- Create: `db/schema.sql`
- Create: `db/__init__.py` (vacío)
- Create: `db/connection.py`
- Create: `providers/__init__.py`, `ingest/__init__.py`, `api/__init__.py`, `tests/__init__.py` (vacíos)

**Interfaces:**
- Consumes: nada.
- Produces: `db.connection.get_connection()` — context manager que abre `psycopg.Connection` contra `DATABASE_URL` (commit al salir limpio). Tablas: `entidades(id, slug UNIQUE, nombre, sigla, sitio_web)`, `tramites(id int PK, nombre, slug, sinonimos text[], descripcion, resultado, marco_legal, entidad_id FK, costo_monto numeric, costo_moneda, costo_concepto, costo_es_gratuito bool, requisitos jsonb, documentos jsonb, ubicaciones jsonb, modalidades jsonb, enlaces jsonb, canal, digitalizado bool, embedding vector(512), last_updated date)`, `categorias(id, slug UNIQUE, nombre)`, `eventos_de_vida(id, nombre UNIQUE)`, puentes `tramites_categorias` y `tramites_eventos`.

- [ ] **Step 1: Crear requirements.txt**

```
fastapi>=0.115
uvicorn>=0.30
anthropic>=0.60
voyageai>=0.3
psycopg[binary]>=3.2
pydantic>=2
python-dotenv>=1.0
pytest>=8
httpx>=0.27
```

- [ ] **Step 2: Crear .env.example**

```
ANTHROPIC_API_KEY=sk-ant-...
VOYAGE_API_KEY=pa-...
DATABASE_URL=postgresql://ami:ami@localhost:5433/ami
MODELO_POTENTE=claude-sonnet-5
MODELO_ECONOMICO=claude-haiku-4-5
MODELO_EMBEDDINGS=voyage-4-lite
EMBEDDING_DIM=512
```

- [ ] **Step 3: Crear docker-compose.yml**

```yaml
services:
  db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: ami
      POSTGRES_PASSWORD: ami
      POSTGRES_DB: ami
    ports:
      - "5433:5432"
    volumes:
      - ./db/schema.sql:/docker-entrypoint-initdb.d/01-schema.sql
      - ami_pgdata:/var/lib/postgresql/data

volumes:
  ami_pgdata:
```

Nota: el script de `docker-entrypoint-initdb.d` solo corre con el volumen vacío. Para re-aplicar el schema después de un cambio: `docker compose exec -T db psql -U ami -d ami < db/schema.sql` (todo el schema es `IF NOT EXISTS`, es idempotente) o `docker compose down -v && docker compose up -d`.

- [ ] **Step 4: Crear db/schema.sql**

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS entidades (
  id serial PRIMARY KEY,
  slug text UNIQUE NOT NULL,
  nombre text NOT NULL,
  sigla text,
  sitio_web text
);

CREATE TABLE IF NOT EXISTS tramites (
  id integer PRIMARY KEY,
  nombre text NOT NULL,
  slug text,
  sinonimos text[] NOT NULL DEFAULT '{}',
  descripcion text,
  resultado text,
  marco_legal text,
  entidad_id integer REFERENCES entidades(id),
  costo_monto numeric,
  costo_moneda text,
  costo_concepto text,
  costo_es_gratuito boolean NOT NULL DEFAULT false,
  requisitos jsonb NOT NULL DEFAULT '[]',
  documentos jsonb NOT NULL DEFAULT '[]',
  ubicaciones jsonb NOT NULL DEFAULT '[]',
  modalidades jsonb NOT NULL DEFAULT '[]',
  enlaces jsonb NOT NULL DEFAULT '[]',
  canal text,
  digitalizado boolean NOT NULL DEFAULT false,
  embedding vector(512),
  last_updated date
);

CREATE TABLE IF NOT EXISTS categorias (
  id serial PRIMARY KEY,
  slug text UNIQUE NOT NULL,
  nombre text NOT NULL
);

CREATE TABLE IF NOT EXISTS tramites_categorias (
  tramite_id integer REFERENCES tramites(id) ON DELETE CASCADE,
  categoria_id integer REFERENCES categorias(id) ON DELETE CASCADE,
  PRIMARY KEY (tramite_id, categoria_id)
);

CREATE TABLE IF NOT EXISTS eventos_de_vida (
  id serial PRIMARY KEY,
  nombre text UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS tramites_eventos (
  tramite_id integer REFERENCES tramites(id) ON DELETE CASCADE,
  evento_id integer REFERENCES eventos_de_vida(id) ON DELETE CASCADE,
  PRIMARY KEY (tramite_id, evento_id)
);

CREATE INDEX IF NOT EXISTS idx_tramites_embedding
  ON tramites USING hnsw (embedding vector_cosine_ops);
```

- [ ] **Step 5: Crear db/connection.py** (+ los `__init__.py` vacíos)

```python
import os
from contextlib import contextmanager

import psycopg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://ami:ami@localhost:5433/ami")


@contextmanager
def get_connection():
    with psycopg.connect(DATABASE_URL) as conn:
        yield conn
```

- [ ] **Step 6: Levantar el entorno y verificar**

Run:
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # y completar las API keys reales
docker compose up -d && sleep 5
docker compose exec db psql -U ami -d ami -c "\dt" \
  && docker compose exec db psql -U ami -d ami -c "SELECT extname FROM pg_extension WHERE extname='vector';"
.venv/bin/python -c "from db.connection import get_connection
with get_connection() as c:
    print(c.execute('SELECT count(*) FROM tramites').fetchone())"
```
Expected: `\dt` lista las 6 tablas; `vector` aparece en pg_extension; el script Python imprime `(0,)`.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt .env.example docker-compose.yml db/ providers/__init__.py ingest/__init__.py api/__init__.py tests/__init__.py
git commit -m "feat: scaffolding con Postgres+pgvector y schema inicial"
```

---

### Task 2: Capa de providers (NVIDIA NIM primario; Anthropic/Voyage alternativos)

**Files:**
- Modify: `requirements.txt` (+ `openai>=1.40`)
- Modify: `.env.example` (proveedor NVIDIA + dims 1024)
- Modify: `db/schema.sql` (`vector(512)` → `vector(1024)`)
- Create: `providers/base.py`
- Create: `providers/openai_compat.py`
- Create: `providers/anthropic_chat.py`
- Create: `providers/voyage_embeddings.py`
- Create: `providers/factory.py`
- Test: `tests/test_providers.py`

**Interfaces:**
- Consumes: env vars del Task 1 (enmendadas acá).
- Produces (contratos que usan Tasks 5, 7, 8, 9):
  - `providers.base.ChatProvider` (Protocol): `complete(*, system: str, messages: list[dict], max_tokens: int = 1024) -> str`; `complete_json(*, system: str, messages: list[dict], schema: dict, max_tokens: int = 1024) -> dict | None`; `stream(*, system: str, messages: list[dict], max_tokens: int = 4096) -> Iterator[str]`. `messages` son dicts `{"role": "user"|"assistant", "content": str}`.
  - `providers.base.EmbeddingProvider` (Protocol): `embed_documents(texts: list[str]) -> list[list[float]]`; `embed_query(text: str) -> list[float]`.
  - `providers.factory.chat_potente() / chat_economico() -> ChatProvider`; `providers.factory.embedder() -> EmbeddingProvider` — instancian NVIDIA u Anthropic/Voyage según `PROVIDER`.

- [ ] **Step 1: Enmienda de infraestructura (proveedor NVIDIA + 1024 dims)**

```bash
printf 'openai>=1.40\n' >> requirements.txt
.venv/bin/pip install -r requirements.txt
sed -i 's/vector(512)/vector(1024)/' db/schema.sql
docker compose down -v && docker compose up -d && sleep 5
docker compose exec db psql -U ami -d ami -c "\dt"
```

Reescribir `.env.example` con este contenido exacto:

```
# Proveedor de modelos: nvidia (default, keys gratis en build.nvidia.com) | anthropic
PROVIDER=nvidia
NVIDIA_API_KEY=nvapi-...
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
DATABASE_URL=postgresql://ami:ami@localhost:5433/ami
MODELO_POTENTE=meta/llama-3.3-70b-instruct
MODELO_ECONOMICO=meta/llama-3.1-8b-instruct
MODELO_EMBEDDINGS=baai/bge-m3
EMBEDDING_DIM=1024
# Solo si PROVIDER=anthropic (usar: claude-sonnet-5 / claude-haiku-4-5 / voyage-4-lite y EMBEDDING_DIM acorde):
ANTHROPIC_API_KEY=
VOYAGE_API_KEY=
```

Actualizar también el `.env` local al mismo formato, **preservando cualquier key real que el usuario ya haya puesto**.

Expected: `openai` instalado; `\dt` lista de nuevo las 6 tablas (volumen recreado desde cero); `grep vector db/schema.sql` muestra `vector(1024)`.

- [ ] **Step 2: Escribir los tests que fallan**

```python
# tests/test_providers.py
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
```

- [ ] **Step 3: Correr y verificar que falla**

Run: `.venv/bin/pytest tests/test_providers.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'providers.anthropic_chat'`.

- [ ] **Step 4: Implementar providers/base.py**

```python
from typing import Iterator, Protocol


class ChatProvider(Protocol):
    def complete(self, *, system: str, messages: list[dict], max_tokens: int = 1024) -> str: ...

    def complete_json(self, *, system: str, messages: list[dict], schema: dict, max_tokens: int = 1024) -> dict | None: ...

    def stream(self, *, system: str, messages: list[dict], max_tokens: int = 4096) -> Iterator[str]: ...


class EmbeddingProvider(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...
```

- [ ] **Step 5: Implementar providers/openai_compat.py (proveedor primario)**

```python
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
            try:
                # guided decoding de NVIDIA NIM (vLLM); no todos los modelos lo soportan
                respuesta = self._client.chat.completions.create(
                    model=self.model,
                    messages=self._mensajes(system, messages),
                    max_tokens=max_tokens,
                    extra_body={"nvext": {"guided_json": schema}},
                )
            except Exception:
                respuesta = self._client.chat.completions.create(
                    model=self.model,
                    messages=self._mensajes(
                        system + "\nRespondé ÚNICAMENTE con un objeto JSON válido, sin texto adicional.",
                        messages,
                    ),
                    max_tokens=max_tokens,
                )
            return _extraer_json(respuesta.choices[0].message.content or "")
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
```

- [ ] **Step 6: Implementar providers/anthropic_chat.py (alternativo)**

```python
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
        """Fail-open: nunca lanza; devuelve None ante cualquier problema."""
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
        except Exception:
            logger.warning("complete_json falló para %s", self.model, exc_info=True)
            return None

    def stream(self, *, system: str, messages: list[dict], max_tokens: int = 4096) -> Iterator[str]:
        with self._client.messages.stream(
            model=self.model, system=system, messages=messages, max_tokens=max_tokens
        ) as s:
            yield from s.text_stream
```

- [ ] **Step 7: Implementar providers/voyage_embeddings.py (alternativo)**

```python
import voyageai

_TAMANO_LOTE = 100


class VoyageEmbeddingProvider:
    def __init__(self, model: str = "voyage-4-lite", output_dimension: int = 512, client=None):
        self.model = model
        self.output_dimension = output_dimension
        self._client = client or voyageai.Client()  # lee VOYAGE_API_KEY del entorno

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectores: list[list[float]] = []
        for i in range(0, len(texts), _TAMANO_LOTE):
            lote = texts[i : i + _TAMANO_LOTE]
            resultado = self._client.embed(
                lote, model=self.model, input_type="document", output_dimension=self.output_dimension
            )
            vectores.extend(resultado.embeddings)
        return vectores

    def embed_query(self, text: str) -> list[float]:
        resultado = self._client.embed(
            [text], model=self.model, input_type="query", output_dimension=self.output_dimension
        )
        return resultado.embeddings[0]
```

- [ ] **Step 8: Implementar providers/factory.py**

```python
import os

from dotenv import load_dotenv

from providers.anthropic_chat import AnthropicChatProvider
from providers.base import ChatProvider, EmbeddingProvider
from providers.openai_compat import OpenAICompatChatProvider, OpenAICompatEmbeddingProvider
from providers.voyage_embeddings import VoyageEmbeddingProvider

load_dotenv()

_NVIDIA_BASE_URL_DEFAULT = "https://integrate.api.nvidia.com/v1"


def _proveedor() -> str:
    return os.environ.get("PROVIDER", "nvidia").lower()


def _nvidia_chat(modelo: str) -> OpenAICompatChatProvider:
    return OpenAICompatChatProvider(
        model=modelo,
        base_url=os.environ.get("NVIDIA_BASE_URL", _NVIDIA_BASE_URL_DEFAULT),
        api_key=os.environ.get("NVIDIA_API_KEY", ""),
    )


def chat_potente() -> ChatProvider:
    if _proveedor() == "anthropic":
        return AnthropicChatProvider(os.environ.get("MODELO_POTENTE", "claude-sonnet-5"))
    return _nvidia_chat(os.environ.get("MODELO_POTENTE", "meta/llama-3.3-70b-instruct"))


def chat_economico() -> ChatProvider:
    if _proveedor() == "anthropic":
        return AnthropicChatProvider(os.environ.get("MODELO_ECONOMICO", "claude-haiku-4-5"))
    return _nvidia_chat(os.environ.get("MODELO_ECONOMICO", "meta/llama-3.1-8b-instruct"))


def embedder() -> EmbeddingProvider:
    if _proveedor() == "anthropic":
        return VoyageEmbeddingProvider(
            model=os.environ.get("MODELO_EMBEDDINGS", "voyage-4-lite"),
            output_dimension=int(os.environ.get("EMBEDDING_DIM", "1024")),
        )
    return OpenAICompatEmbeddingProvider(
        model=os.environ.get("MODELO_EMBEDDINGS", "baai/bge-m3"),
        base_url=os.environ.get("NVIDIA_BASE_URL", _NVIDIA_BASE_URL_DEFAULT),
        api_key=os.environ.get("NVIDIA_API_KEY", ""),
    )
```

- [ ] **Step 9: Correr los tests**

Run: `.venv/bin/pytest tests/test_providers.py -v`
Expected: 10 passed.

- [ ] **Step 10: Smoke test real contra NVIDIA NIM (solo si hay key real en .env)**

Run:
```bash
if grep -Eq '^NVIDIA_API_KEY=nvapi-[A-Za-z0-9_-]{20,}' .env; then
  .venv/bin/python - <<'EOF'
from providers import factory
print(factory.chat_economico().complete(system="Respondé en una sola palabra.", messages=[{"role": "user", "content": "¿Capital administrativa de Bolivia?"}]))
v = factory.embedder().embed_query("carnet de identidad")
print(len(v), type(v[0]))
EOF
else
  echo "SIN NVIDIA_API_KEY real en .env — smoke test diferido; reportar como concern"
fi
```
Expected: una palabra tipo `La Paz` y `1024 <class 'float'>` — o el mensaje de diferido si aún no hay key (no es un fallo del task; se re-corre cuando el usuario la agregue).

- [ ] **Step 11: Commit**

```bash
git add providers/ tests/test_providers.py requirements.txt .env.example db/schema.sql
git commit -m "feat: capa de providers con NVIDIA NIM primario y Anthropic/Voyage alternativos"
```

---

### Task 3: Mapper del dataset (funciones puras)

**Files:**
- Create: `ingest/mapper.py`
- Test: `tests/test_mapper.py`

**Interfaces:**
- Consumes: nada (funciones puras sobre dicts del jsonl).
- Produces (contratos que usan Tasks 4, 5):
  - `mapear_tramite(record: dict) -> dict` — devuelve fila con claves: `id` (int), `nombre`, `slug`, `sinonimos` (list[str]), `descripcion`, `resultado`, `marco_legal`, `canal` (`"presencial"|"virtual"|"ambos"|None`), `digitalizado` (bool), `requisitos` (list[dict]), `documentos` (list), `ubicaciones` (list), `modalidades` (list), `enlaces` (list), `last_updated` (date|None), `costo_monto` (float|None), `costo_moneda`, `costo_concepto`, `costo_es_gratuito` (bool), `necesita_llm` (bool), `entidad` (dict con slug/nombre/sigla/sitio_web), `categorias` (list[dict] con slug/nombre), `eventos` (list[str]).
  - `texto_para_embedding(fila: dict) -> str` — nombre + descripcion + sinónimos.
  - Auxiliares testeables: `strip_html`, `mapear_costo`, `mapear_canal`, `aplanar_requisitos`, `parsear_fecha`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_mapper.py
from datetime import date

from ingest.mapper import (
    aplanar_requisitos,
    mapear_canal,
    mapear_costo,
    mapear_tramite,
    parsear_fecha,
    strip_html,
    texto_para_embedding,
)


def _record_base(**overrides):
    record = {
        "id": "1002",
        "estado": "PUBLICADO",
        "fechaActualización": "22/10/2025",
        "nombre": "SOLICITUD DE COPIAS SIMPLES",
        "slug": "solicitud-de-copias-simples",
        "descripcion": "Copias simples de documentos aduaneros.",
        "marcoLegal": "RD 01-096-24",
        "palabrasClave": None,
        "esPresencial": False,
        "esVirtual": True,
        "entidad": {"nombre": "Aduana Nacional", "sigla": "AN", "slug": "aduana-nacional", "urlSitioWeb": "https://www.aduana.gob.bo/"},
        "resultado": "Registro",
        "enlaces": [],
        "categorias": [{"nombre": "Empresas", "slug": "empresas"}],
        "eventosVida": ["Empleo"],
        "documentos": ["COPIA SIMPLE"],
        "ubicaciones": [],
        "modalidades": [
            {
                "tipo": "VIRTUAL",
                "url": "https://jarembae.aduana.gob.bo/",
                "urlSeguimiento": None,
                "publico": [
                    {
                        "tieneCosto": True,
                        "procedimiento": "<p><strong>Paso 1</strong></p>",
                        "costos": [{"conceptoPago": "158", "costo": "1.00", "moneda": "UFV"}],
                        "requisitos": [{"comentario": None, "nombre": "Carta de Solicitud", "entidad": None, "entidadActiva": True}],
                    }
                ],
            }
        ],
    }
    record.update(overrides)
    return record


def test_strip_html():
    assert strip_html("<p><strong>Paso  1</strong> ir</p>") == "Paso 1 ir"
    assert strip_html(None) is None


def test_parsear_fecha():
    assert parsear_fecha("22/10/2025") == date(2025, 10, 22)
    assert parsear_fecha("basura") is None


def test_mapear_canal():
    assert mapear_canal({"esPresencial": True, "esVirtual": True}) == "ambos"
    assert mapear_canal({"esPresencial": False, "esVirtual": True}) == "virtual"
    assert mapear_canal({"esPresencial": False, "esVirtual": False}) is None


def test_mapear_costo_directo():
    costo = mapear_costo(_record_base())
    assert costo == {
        "costo_monto": 1.0,
        "costo_moneda": "UFV",
        "costo_concepto": "158",
        "costo_es_gratuito": False,
        "necesita_llm": False,
    }


def test_mapear_costo_gratuito():
    record = _record_base()
    record["modalidades"][0]["publico"][0].update({"tieneCosto": False, "costos": []})
    costo = mapear_costo(record)
    assert costo["costo_es_gratuito"] is True
    assert costo["necesita_llm"] is False
    assert costo["costo_monto"] is None


def test_mapear_costo_necesita_llm():
    record = _record_base()
    record["modalidades"][0]["publico"][0]["costos"] = []
    costo = mapear_costo(record)
    assert costo["costo_es_gratuito"] is False
    assert costo["necesita_llm"] is True


def test_aplanar_requisitos_dedupe():
    record = _record_base()
    record["modalidades"].append(
        {"tipo": "PRESENCIAL", "url": None, "urlSeguimiento": None,
         "publico": [{"tieneCosto": False, "costos": [],
                      "requisitos": [{"nombre": "Carta de Solicitud", "comentario": "<b>original</b>"},
                                     {"nombre": "Cédula de identidad", "comentario": None}]}]}
    )
    requisitos = aplanar_requisitos(record)
    nombres = [r["nombre"] for r in requisitos]
    assert nombres == ["Carta de Solicitud", "Cédula de identidad"]
    assert requisitos[0]["comentario"] == "original"


def test_mapear_tramite_completo():
    fila = mapear_tramite(_record_base())
    assert fila["id"] == 1002
    assert fila["sinonimos"] == []
    assert fila["canal"] == "virtual"
    assert fila["digitalizado"] is True
    assert fila["eventos"] == ["Empleo"]
    assert fila["categorias"] == [{"slug": "empresas", "nombre": "Empresas"}]
    assert fila["entidad"]["sitio_web"] == "https://www.aduana.gob.bo/"
    assert fila["modalidades"][0]["publico"][0]["procedimiento"] == "Paso 1"
    assert fila["last_updated"] == date(2025, 10, 22)


def test_texto_para_embedding():
    fila = mapear_tramite(_record_base(palabrasClave=["copias", "aduana"]))
    texto = texto_para_embedding(fila)
    assert "SOLICITUD DE COPIAS SIMPLES" in texto
    assert "copias aduana" in texto
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `.venv/bin/pytest tests/test_mapper.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'ingest.mapper'`.

- [ ] **Step 3: Implementar ingest/mapper.py**

```python
import re
from datetime import date, datetime


def strip_html(texto: str | None) -> str | None:
    if texto is None:
        return None
    sin_tags = re.sub(r"<[^>]+>", " ", texto)
    limpio = re.sub(r"\s+", " ", sin_tags).strip()
    return limpio or None


def parsear_fecha(texto: str | None) -> date | None:
    if not texto:
        return None
    try:
        return datetime.strptime(texto, "%d/%m/%Y").date()
    except ValueError:
        return None


def mapear_canal(record: dict) -> str | None:
    presencial = bool(record.get("esPresencial"))
    virtual = bool(record.get("esVirtual"))
    if presencial and virtual:
        return "ambos"
    if virtual:
        return "virtual"
    if presencial:
        return "presencial"
    return None


def _publicos(record: dict):
    for modalidad in record.get("modalidades") or []:
        yield from modalidad.get("publico") or []


def mapear_costo(record: dict) -> dict:
    """Regla de costo (ver DECISIONS.md):
    - costos[] con monto parseable -> mapeo directo
    - ningún público con tieneCosto -> gratuito
    - tieneCosto=true sin costos -> fallback LLM (necesita_llm)
    """
    sin_costo = {"costo_monto": None, "costo_moneda": None, "costo_concepto": None}
    tiene_costo = False
    for publico in _publicos(record):
        tiene_costo = tiene_costo or bool(publico.get("tieneCosto"))
        for costo in publico.get("costos") or []:
            try:
                monto = float(costo.get("costo"))
            except (TypeError, ValueError):
                continue
            return {
                "costo_monto": monto,
                "costo_moneda": costo.get("moneda"),
                "costo_concepto": strip_html(costo.get("conceptoPago")),
                "costo_es_gratuito": False,
                "necesita_llm": False,
            }
    if not tiene_costo:
        return {**sin_costo, "costo_es_gratuito": True, "necesita_llm": False}
    return {**sin_costo, "costo_es_gratuito": False, "necesita_llm": True}


def aplanar_requisitos(record: dict) -> list[dict]:
    vistos: dict[str, dict] = {}
    for publico in _publicos(record):
        for requisito in publico.get("requisitos") or []:
            nombre = (requisito.get("nombre") or "").strip()
            if not nombre:
                continue
            comentario = strip_html(requisito.get("comentario"))
            if nombre not in vistos:
                vistos[nombre] = {"nombre": nombre, "comentario": comentario}
            elif vistos[nombre]["comentario"] is None and comentario:
                vistos[nombre]["comentario"] = comentario
    return list(vistos.values())


def _limpiar_modalidad(modalidad: dict) -> dict:
    publicos = [
        {**p, "procedimiento": strip_html(p.get("procedimiento"))}
        for p in modalidad.get("publico") or []
    ]
    return {**modalidad, "publico": publicos}


def mapear_tramite(record: dict) -> dict:
    entidad = record["entidad"]
    return {
        "id": int(record["id"]),
        "nombre": record["nombre"],
        "slug": record.get("slug"),
        "sinonimos": record.get("palabrasClave") or [],
        "descripcion": (record.get("descripcion") or "").strip(),
        "resultado": record.get("resultado"),
        "marco_legal": record.get("marcoLegal"),
        "canal": mapear_canal(record),
        "digitalizado": bool(record.get("esVirtual")),
        "requisitos": aplanar_requisitos(record),
        "documentos": record.get("documentos") or [],
        "ubicaciones": record.get("ubicaciones") or [],
        "modalidades": [_limpiar_modalidad(m) for m in record.get("modalidades") or []],
        "enlaces": record.get("enlaces") or [],
        "last_updated": parsear_fecha(record.get("fechaActualización")),
        "entidad": {
            "slug": entidad["slug"],
            "nombre": entidad["nombre"],
            "sigla": entidad.get("sigla"),
            "sitio_web": entidad.get("urlSitioWeb"),
        },
        "categorias": [{"slug": c["slug"], "nombre": c["nombre"]} for c in record.get("categorias") or []],
        "eventos": record.get("eventosVida") or [],
        **mapear_costo(record),
    }


def texto_para_embedding(fila: dict) -> str:
    partes = [fila["nombre"], fila.get("descripcion") or "", " ".join(fila.get("sinonimos") or [])]
    return "\n".join(p for p in partes if p).strip()
```

- [ ] **Step 4: Correr los tests**

Run: `.venv/bin/pytest tests/test_mapper.py -v`
Expected: 9 passed.

- [ ] **Step 5: Validar contra el dataset real completo**

Run:
```bash
curl -sL https://raw.githubusercontent.com/datosbolivia/tramites-bo/main/tramites.jsonl -o /tmp/tramites.jsonl
.venv/bin/python -c "
import json
from ingest.mapper import mapear_tramite
filas = [mapear_tramite(json.loads(l)) for l in open('/tmp/tramites.jsonl')]
print('filas:', len(filas))
print('necesitan LLM:', sum(1 for f in filas if f['necesita_llm']))
print('gratuitos:', sum(1 for f in filas if f['costo_es_gratuito']))
print('con costo directo:', sum(1 for f in filas if f['costo_monto'] is not None))"
```
Expected: `filas: 1739` (o el total actual del dataset), `necesitan LLM:` ~23, sin excepciones.

- [ ] **Step 6: Commit**

```bash
git add ingest/mapper.py tests/test_mapper.py
git commit -m "feat: mapper puro del dataset tramites-bo al esquema de la DB"
```

---

### Task 4: Capa de acceso a datos (upserts + retrieval)

**Files:**
- Create: `db/queries.py`
- Test: `tests/test_queries.py` (integración — requiere `docker compose up -d`)

**Interfaces:**
- Consumes: `get_connection()` (Task 1), fila de `mapear_tramite` (Task 3).
- Produces (contratos que usan Tasks 5, 7, 9):
  - `guardar_tramite_completo(conn, fila: dict, embedding: list[float] | None) -> None` — upsertea entidad, trámite, categorías, eventos y puentes. Ignora las claves `necesita_llm`/`entidad`/`categorias`/`eventos` como columnas.
  - `buscar_tramites(conn, embedding: list[float], categoria_slug: str | None = None, entidad_slug: str | None = None, evento_nombre: str | None = None, limit: int = 5) -> list[dict]` — cada dict trae todas las columnas del trámite + `entidad_nombre`, `entidad_sitio_web`, `distancia` (float, coseno). Ordenado por distancia ascendente.
  - `listar_categorias(conn) -> list[dict]` (`{slug, nombre}`), `listar_eventos(conn) -> list[str]`, `buscar_entidad_slug(conn, texto: str) -> str | None`.

- [ ] **Step 1: Escribir el test de integración que falla**

```python
# tests/test_queries.py
"""Tests de integración: requieren `docker compose up -d` corriendo."""
import pytest

from db.connection import get_connection
from db.queries import (
    buscar_entidad_slug,
    buscar_tramites,
    guardar_tramite_completo,
    listar_categorias,
    listar_eventos,
)

DIM = 1024


def _fila(id_, nombre, **overrides):
    fila = {
        "id": id_, "nombre": nombre, "slug": f"slug-{id_}", "sinonimos": ["prueba"],
        "descripcion": "desc", "resultado": "res", "marco_legal": None,
        "canal": "virtual", "digitalizado": True,
        "requisitos": [{"nombre": "CI", "comentario": None}], "documentos": [],
        "ubicaciones": [], "modalidades": [], "enlaces": [],
        "last_updated": None,
        "costo_monto": 10.0, "costo_moneda": "Bs", "costo_concepto": "pago",
        "costo_es_gratuito": False, "necesita_llm": False,
        "entidad": {"slug": "segip-test", "nombre": "SEGIP TEST", "sigla": "SGT", "sitio_web": None},
        "categorias": [{"slug": "cat-test", "nombre": "Cat Test"}],
        "eventos": ["Evento Test"],
    }
    fila.update(overrides)
    return fila


def _vec(valor_ultimo: float) -> list[float]:
    return [0.0] * (DIM - 1) + [valor_ultimo]


@pytest.fixture()
def conn():
    with get_connection() as c:
        yield c
        c.execute("DELETE FROM tramites WHERE id >= 900000")
        c.execute("DELETE FROM entidades WHERE slug = 'segip-test'")
        c.execute("DELETE FROM categorias WHERE slug = 'cat-test'")
        c.execute("DELETE FROM eventos_de_vida WHERE nombre = 'Evento Test'")
        c.commit()


def test_guardar_y_buscar(conn):
    guardar_tramite_completo(conn, _fila(900001, "TRAMITE CERCANO"), _vec(1.0))
    guardar_tramite_completo(conn, _fila(900002, "TRAMITE LEJANO"), _vec(-1.0))
    conn.commit()

    # limit alto: la DB puede tener datos reales cargados entre medio de las filas de prueba
    hits = [h for h in buscar_tramites(conn, _vec(1.0), limit=5000) if h["id"] >= 900000]
    assert [h["nombre"] for h in hits] == ["TRAMITE CERCANO", "TRAMITE LEJANO"]
    assert hits[0]["distancia"] < hits[1]["distancia"]
    assert hits[0]["entidad_nombre"] == "SEGIP TEST"


def test_upsert_idempotente(conn):
    guardar_tramite_completo(conn, _fila(900003, "NOMBRE VIEJO"), _vec(0.5))
    guardar_tramite_completo(conn, _fila(900003, "NOMBRE NUEVO"), _vec(0.5))
    conn.commit()
    nombre = conn.execute("SELECT nombre FROM tramites WHERE id = 900003").fetchone()[0]
    assert nombre == "NOMBRE NUEVO"


def test_filtros_y_catalogos(conn):
    guardar_tramite_completo(conn, _fila(900004, "CON CATEGORIA"), _vec(1.0))
    conn.commit()

    assert {"slug": "cat-test", "nombre": "Cat Test"} in listar_categorias(conn)
    assert "Evento Test" in listar_eventos(conn)
    assert buscar_entidad_slug(conn, "SGT") == "segip-test"
    assert buscar_entidad_slug(conn, "segip te") == "segip-test"
    assert buscar_entidad_slug(conn, "no-existe-xyz") is None

    con_filtro = buscar_tramites(conn, _vec(1.0), categoria_slug="cat-test")
    assert any(h["id"] == 900004 for h in con_filtro)
    sin_match = buscar_tramites(conn, _vec(1.0), categoria_slug="categoria-inexistente")
    assert sin_match == []
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `docker compose up -d && .venv/bin/pytest tests/test_queries.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'db.queries'`.

- [ ] **Step 3: Implementar db/queries.py**

```python
import json

from psycopg.types.json import Json

_COLUMNAS_TRAMITE = [
    "id", "nombre", "slug", "sinonimos", "descripcion", "resultado", "marco_legal",
    "entidad_id", "costo_monto", "costo_moneda", "costo_concepto", "costo_es_gratuito",
    "requisitos", "documentos", "ubicaciones", "modalidades", "enlaces",
    "canal", "digitalizado", "embedding", "last_updated",
]


def _vector_literal(embedding: list[float] | None) -> str | None:
    if embedding is None:
        return None
    return "[" + ",".join(map(str, embedding)) + "]"


def upsert_entidad(conn, entidad: dict) -> int:
    fila = conn.execute(
        """
        INSERT INTO entidades (slug, nombre, sigla, sitio_web)
        VALUES (%(slug)s, %(nombre)s, %(sigla)s, %(sitio_web)s)
        ON CONFLICT (slug) DO UPDATE
          SET nombre = EXCLUDED.nombre, sigla = EXCLUDED.sigla, sitio_web = EXCLUDED.sitio_web
        RETURNING id
        """,
        entidad,
    ).fetchone()
    return fila[0]


def upsert_categoria(conn, categoria: dict) -> int:
    fila = conn.execute(
        """
        INSERT INTO categorias (slug, nombre) VALUES (%(slug)s, %(nombre)s)
        ON CONFLICT (slug) DO UPDATE SET nombre = EXCLUDED.nombre
        RETURNING id
        """,
        categoria,
    ).fetchone()
    return fila[0]


def upsert_evento(conn, nombre: str) -> int:
    fila = conn.execute(
        """
        INSERT INTO eventos_de_vida (nombre) VALUES (%(nombre)s)
        ON CONFLICT (nombre) DO UPDATE SET nombre = EXCLUDED.nombre
        RETURNING id
        """,
        {"nombre": nombre},
    ).fetchone()
    return fila[0]


def guardar_tramite_completo(conn, fila: dict, embedding: list[float] | None) -> None:
    entidad_id = upsert_entidad(conn, fila["entidad"])
    params = {
        **{k: fila.get(k) for k in _COLUMNAS_TRAMITE if k not in ("entidad_id", "embedding")},
        "entidad_id": entidad_id,
        "embedding": _vector_literal(embedding),
        "requisitos": Json(fila["requisitos"]),
        "documentos": Json(fila["documentos"]),
        "ubicaciones": Json(fila["ubicaciones"]),
        "modalidades": Json(fila["modalidades"]),
        "enlaces": Json(fila["enlaces"]),
    }
    columnas = ", ".join(_COLUMNAS_TRAMITE)
    placeholders = ", ".join(
        f"%({c})s::vector" if c == "embedding" else f"%({c})s" for c in _COLUMNAS_TRAMITE
    )
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in _COLUMNAS_TRAMITE if c != "id")
    conn.execute(
        f"INSERT INTO tramites ({columnas}) VALUES ({placeholders}) "
        f"ON CONFLICT (id) DO UPDATE SET {updates}",
        params,
    )

    conn.execute("DELETE FROM tramites_categorias WHERE tramite_id = %s", (fila["id"],))
    for categoria in fila["categorias"]:
        cat_id = upsert_categoria(conn, categoria)
        conn.execute(
            "INSERT INTO tramites_categorias (tramite_id, categoria_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (fila["id"], cat_id),
        )

    conn.execute("DELETE FROM tramites_eventos WHERE tramite_id = %s", (fila["id"],))
    for evento in fila["eventos"]:
        ev_id = upsert_evento(conn, evento)
        conn.execute(
            "INSERT INTO tramites_eventos (tramite_id, evento_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (fila["id"], ev_id),
        )


def listar_categorias(conn) -> list[dict]:
    filas = conn.execute("SELECT slug, nombre FROM categorias ORDER BY nombre").fetchall()
    return [{"slug": f[0], "nombre": f[1]} for f in filas]


def listar_eventos(conn) -> list[str]:
    return [f[0] for f in conn.execute("SELECT nombre FROM eventos_de_vida ORDER BY nombre").fetchall()]


def buscar_entidad_slug(conn, texto: str) -> str | None:
    fila = conn.execute(
        """
        SELECT slug FROM entidades
        WHERE sigla ILIKE %(t)s OR nombre ILIKE '%%' || %(t)s || '%%'
        ORDER BY (sigla ILIKE %(t)s) DESC
        LIMIT 1
        """,
        {"t": texto},
    ).fetchone()
    return fila[0] if fila else None


_SQL_BUSCAR = """
SELECT t.id, t.nombre, t.slug, t.descripcion, t.resultado, t.marco_legal,
       t.canal, t.digitalizado,
       t.costo_monto, t.costo_moneda, t.costo_concepto, t.costo_es_gratuito,
       t.requisitos, t.documentos, t.ubicaciones, t.modalidades, t.enlaces,
       e.nombre AS entidad_nombre, e.sitio_web AS entidad_sitio_web,
       t.embedding <=> %(emb)s::vector AS distancia
FROM tramites t
LEFT JOIN entidades e ON e.id = t.entidad_id
WHERE t.embedding IS NOT NULL
  AND (%(cat)s::text IS NULL OR EXISTS (
        SELECT 1 FROM tramites_categorias tc JOIN categorias c ON c.id = tc.categoria_id
        WHERE tc.tramite_id = t.id AND c.slug = %(cat)s))
  AND (%(ent)s::text IS NULL OR e.slug = %(ent)s)
  AND (%(ev)s::text IS NULL OR EXISTS (
        SELECT 1 FROM tramites_eventos te JOIN eventos_de_vida ev ON ev.id = te.evento_id
        WHERE te.tramite_id = t.id AND ev.nombre = %(ev)s))
ORDER BY t.embedding <=> %(emb)s::vector
LIMIT %(limit)s
"""


def buscar_tramites(
    conn,
    embedding: list[float],
    categoria_slug: str | None = None,
    entidad_slug: str | None = None,
    evento_nombre: str | None = None,
    limit: int = 5,
) -> list[dict]:
    cursor = conn.execute(
        _SQL_BUSCAR,
        {
            "emb": _vector_literal(embedding),
            "cat": categoria_slug,
            "ent": entidad_slug,
            "ev": evento_nombre,
            "limit": limit,
        },
    )
    nombres = [d.name for d in cursor.description]
    filas = []
    for tupla in cursor.fetchall():
        fila = dict(zip(nombres, tupla))
        fila["distancia"] = float(fila["distancia"])
        if fila["costo_monto"] is not None:
            fila["costo_monto"] = float(fila["costo_monto"])
        filas.append(fila)
    return filas
```

- [ ] **Step 4: Correr los tests**

Run: `.venv/bin/pytest tests/test_queries.py -v`
Expected: 3 passed (con el contenedor de Postgres corriendo).

- [ ] **Step 5: Commit**

```bash
git add db/queries.py tests/test_queries.py
git commit -m "feat: upserts y retrieval vectorial con filtros de metadata"
```

---

### Task 5: Extracción de costo con LLM + script de ingesta

**Files:**
- Create: `ingest/costo_llm.py`
- Create: `ingest/load.py`
- Test: `tests/test_costo_llm.py`

**Interfaces:**
- Consumes: `ChatProvider` (Task 2), `mapear_tramite`/`texto_para_embedding` (Task 3), `guardar_tramite_completo` (Task 4), `factory` (Task 2).
- Produces: `ingest.costo_llm.extraer_costo(chat, descripcion: str, resultado: str | None) -> dict | None` (claves `monto` float, `moneda` str, `concepto` str|None; None si no hay costo o falló). Script CLI `python -m ingest.load [--jsonl PATH] [--limit N] [--skip-llm] [--skip-embeddings]`.

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/test_costo_llm.py
from ingest.costo_llm import extraer_costo


class FakeChat:
    def __init__(self, respuesta):
        self._respuesta = respuesta
        self.ultimo_schema = None

    def complete_json(self, *, system, messages, schema, max_tokens=1024):
        self.ultimo_schema = schema
        return self._respuesta


def test_extraer_costo_encontrado():
    chat = FakeChat({"monto": 20.0, "moneda": "Bs", "concepto": "formulario"})
    datos = extraer_costo(chat, "El trámite cuesta 20 bolivianos por formulario.", "Registro")
    assert datos == {"monto": 20.0, "moneda": "Bs", "concepto": "formulario"}
    assert chat.ultimo_schema["additionalProperties"] is False


def test_extraer_costo_sin_monto_devuelve_none():
    assert extraer_costo(FakeChat({"monto": None, "moneda": None, "concepto": None}), "d", "r") is None
    assert extraer_costo(FakeChat(None), "d", "r") is None
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `.venv/bin/pytest tests/test_costo_llm.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'ingest.costo_llm'`.

- [ ] **Step 3: Implementar ingest/costo_llm.py**

```python
from providers.base import ChatProvider

SCHEMA_COSTO = {
    "type": "object",
    "properties": {
        "monto": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "moneda": {"anyOf": [{"type": "string", "enum": ["Bs", "USD", "UFV"]}, {"type": "null"}]},
        "concepto": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": ["monto", "moneda", "concepto"],
    "additionalProperties": False,
}

SISTEMA_COSTO = """Sos un extractor de datos de trámites del Estado boliviano.
Dado el texto de un trámite, extraé el costo monetario para el ciudadano si está mencionado explícitamente.
Reglas:
- monto: número exacto mencionado en el texto, o null si no se menciona ningún monto.
- moneda: "Bs" (bolivianos), "USD" (dólares) o "UFV". Si el texto no la aclara, asumí "Bs".
- concepto: descripción breve de qué se paga, o null.
- NO inventes montos. Si el texto no menciona un costo concreto, devolvé monto null."""


def extraer_costo(chat: ChatProvider, descripcion: str, resultado: str | None) -> dict | None:
    contenido = f"Descripción del trámite:\n{descripcion}\n\nResultado del trámite:\n{resultado or '(sin dato)'}"
    datos = chat.complete_json(
        system=SISTEMA_COSTO,
        messages=[{"role": "user", "content": contenido}],
        schema=SCHEMA_COSTO,
        max_tokens=300,
    )
    if not datos or datos.get("monto") is None:
        return None
    return datos
```

- [ ] **Step 4: Correr los tests**

Run: `.venv/bin/pytest tests/test_costo_llm.py -v`
Expected: 2 passed.

- [ ] **Step 5: Implementar ingest/load.py**

```python
"""Carga única del dataset tramites-bo a Postgres.

Uso:
    python -m ingest.load                      # descarga y carga todo
    python -m ingest.load --limit 30 --skip-llm --skip-embeddings   # prueba rápida
"""
import argparse
import json
import logging
import urllib.request

from dotenv import load_dotenv

from db.connection import get_connection
from db.queries import guardar_tramite_completo
from ingest.costo_llm import extraer_costo
from ingest.mapper import mapear_tramite, texto_para_embedding
from providers import factory

URL_TRAMITES = "https://raw.githubusercontent.com/datosbolivia/tramites-bo/main/tramites.jsonl"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def leer_registros(ruta: str | None) -> list[dict]:
    if ruta:
        with open(ruta, encoding="utf-8") as f:
            lineas = f.readlines()
    else:
        logger.info("descargando %s", URL_TRAMITES)
        with urllib.request.urlopen(URL_TRAMITES) as respuesta:
            lineas = respuesta.read().decode("utf-8").splitlines()
    return [json.loads(linea) for linea in lineas if linea.strip()]


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--jsonl", default=None, help="ruta local a tramites.jsonl (default: descarga)")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip-llm", action="store_true")
    parser.add_argument("--skip-embeddings", action="store_true")
    args = parser.parse_args()

    registros = leer_registros(args.jsonl)
    if args.limit:
        registros = registros[: args.limit]

    filas = []
    for registro in registros:
        try:
            filas.append(mapear_tramite(registro))
        except Exception:
            logger.exception("registro %s falló en el mapeo, se salta", registro.get("id"))
    logger.info("mapeadas %d filas (%d necesitan LLM para costo)", len(filas), sum(f["necesita_llm"] for f in filas))

    if not args.skip_llm:
        chat = factory.chat_potente()
        for fila in filas:
            if not fila["necesita_llm"]:
                continue
            datos = extraer_costo(chat, fila["descripcion"], fila["resultado"])
            if datos:
                fila.update(
                    costo_monto=datos["monto"], costo_moneda=datos["moneda"], costo_concepto=datos["concepto"]
                )
                logger.info("costo extraído para %s: %s %s", fila["id"], datos["monto"], datos["moneda"])
            else:
                logger.info("sin costo extraíble para %s", fila["id"])

    vectores: list[list[float] | None] = [None] * len(filas)
    if not args.skip_embeddings:
        emb = factory.embedder()
        logger.info("generando %d embeddings...", len(filas))
        vectores = emb.embed_documents([texto_para_embedding(f) for f in filas])

    guardadas = 0
    with get_connection() as conn:
        for fila, vector in zip(filas, vectores):
            try:
                guardar_tramite_completo(conn, fila, vector)
                conn.commit()
                guardadas += 1
            except Exception:
                conn.rollback()
                logger.exception("trámite %s falló al guardar, se salta", fila["id"])
    logger.info("guardadas %d/%d filas", guardadas, len(filas))


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Corrida de prueba limitada (sin costos de API de chat)**

Run:
```bash
.venv/bin/python -m ingest.load --jsonl /tmp/tramites.jsonl --limit 30 --skip-llm
docker compose exec db psql -U ami -d ami -c "SELECT count(*), count(embedding) FROM tramites;"
docker compose exec db psql -U ami -d ami -c "SELECT nombre, costo_monto, costo_moneda, canal FROM tramites LIMIT 5;"
```
Expected: `guardadas 30/30`; count=30 con 30 embeddings; filas con datos coherentes. (Usa `/tmp/tramites.jsonl` del Task 3 Step 5; si no existe, quitar `--jsonl` para que descargue.)

- [ ] **Step 7: Commit**

```bash
git add ingest/costo_llm.py ingest/load.py tests/test_costo_llm.py
git commit -m "feat: extraccion de costo con LLM y script de ingesta"
```

---

### Task 6: Gate de confianza (código puro)

**Files:**
- Create: `api/confidence.py`
- Test: `tests/test_confidence.py`

**Interfaces:**
- Consumes: nada.
- Produces (usa Task 7): `api.confidence.evaluar_confianza(distancias: list[float], umbral_gap: float = UMBRAL_GAP, umbral_distancia_max: float = UMBRAL_DISTANCIA_MAX) -> Literal["claro", "ambiguo", "vacio"]`. Constantes `UMBRAL_GAP = 0.05`, `UMBRAL_DISTANCIA_MAX = 0.55` (valores de partida — se calibran en Task 9).

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_confidence.py
from api.confidence import evaluar_confianza


def test_vacio():
    assert evaluar_confianza([]) == "vacio"


def test_claro_con_gap_grande():
    assert evaluar_confianza([0.20, 0.40, 0.45]) == "claro"


def test_ambiguo_con_gap_chico():
    assert evaluar_confianza([0.30, 0.32, 0.45]) == "ambiguo"


def test_ambiguo_si_top1_esta_lejos():
    # aunque el gap sea grande, si el mejor resultado está lejos no hay confianza
    assert evaluar_confianza([0.80, 0.99]) == "ambiguo"


def test_claro_resultado_unico_cercano():
    assert evaluar_confianza([0.20]) == "claro"


def test_umbral_es_parametrizable():
    assert evaluar_confianza([0.30, 0.32], umbral_gap=0.01) == "claro"
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `.venv/bin/pytest tests/test_confidence.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'api.confidence'`.

- [ ] **Step 3: Implementar api/confidence.py**

```python
from typing import Literal

# Valores de partida — calibrar con tests/eval_retrieval.py (ver Task 9).
UMBRAL_GAP = 0.05
UMBRAL_DISTANCIA_MAX = 0.55


def evaluar_confianza(
    distancias: list[float],
    umbral_gap: float = UMBRAL_GAP,
    umbral_distancia_max: float = UMBRAL_DISTANCIA_MAX,
) -> Literal["claro", "ambiguo", "vacio"]:
    """Gate determinista sobre las distancias coseno del retrieval (ascendentes)."""
    if not distancias:
        return "vacio"
    if distancias[0] > umbral_distancia_max:
        return "ambiguo"
    if len(distancias) == 1:
        return "claro"
    if distancias[1] - distancias[0] >= umbral_gap:
        return "claro"
    return "ambiguo"
```

- [ ] **Step 4: Correr los tests**

Run: `.venv/bin/pytest tests/test_confidence.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add api/confidence.py tests/test_confidence.py
git commit -m "feat: gate de confianza determinista sobre distancias de retrieval"
```

---

### Task 7: Pipeline online (filtros, aclaración, síntesis) + estado de conversación

**Files:**
- Create: `api/conversations.py`
- Create: `api/prompts.py`
- Create: `api/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: providers (Task 2), `buscar_tramites`/`listar_*`/`buscar_entidad_slug` (Task 4), `evaluar_confianza` (Task 6), `get_connection` (Task 1).
- Produces (usa Task 8):
  - `api.conversations.ConversationStore`: `get_or_create(conversation_id: str | None) -> str`; `append(cid, role, content)`; `mensajes(cid) -> list[dict]`; `texto_de_consulta(cid) -> str` (concatena los mensajes user).
  - `api.pipeline.Deps` (dataclass): `chat_economico`, `chat_potente`, `embedder`, `store`, `catalogos` (dict `{"categorias": list[str], "eventos": list[str]}`).
  - `api.pipeline.procesar_mensaje(deps: Deps, conversation_id: str, mensaje: str) -> Iterator[tuple[str, dict]]` — yields `("clarification", {"text"})`, `("answer", {"delta"})`, `("answer", {"done": True, "tramite_ids": list[int]})`, `("error", {"message"})`.
  - `api.pipeline.fetch_live_fallback(consulta: str) -> None` — stub (fase futura).

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_pipeline.py
from contextlib import contextmanager

import api.pipeline as pipeline
from api.conversations import ConversationStore
from api.pipeline import Deps, procesar_mensaje


class FakeChat:
    def __init__(self, json_result=None, texto="¿Te referís a A o B?", deltas=("Hola", " mundo")):
        self._json = json_result
        self._texto = texto
        self._deltas = deltas

    def complete(self, *, system, messages, max_tokens=1024):
        return self._texto

    def complete_json(self, *, system, messages, schema, max_tokens=1024):
        return self._json

    def stream(self, *, system, messages, max_tokens=4096):
        yield from self._deltas


class FakeEmbedder:
    def embed_documents(self, texts):
        return [[0.0] * 1024 for _ in texts]

    def embed_query(self, text):
        return [0.0] * 1024


def _deps(**kwargs):
    return Deps(
        chat_economico=kwargs.get("economico", FakeChat(json_result=None)),
        chat_potente=kwargs.get("potente", FakeChat()),
        embedder=FakeEmbedder(),
        store=ConversationStore(),
        catalogos={"categorias": ["empresas"], "eventos": ["Empleo"]},
    )


def _hit(id_, nombre, distancia):
    return {"id": id_, "nombre": nombre, "descripcion": "d", "resultado": "r", "marco_legal": None,
            "canal": "virtual", "digitalizado": True, "costo_monto": 10.0, "costo_moneda": "Bs",
            "costo_concepto": None, "costo_es_gratuito": False, "requisitos": [], "documentos": [],
            "ubicaciones": [], "modalidades": [], "enlaces": [], "entidad_nombre": "SEGIP",
            "entidad_sitio_web": None, "distancia": distancia}


@contextmanager
def _conn_fake():
    yield None


def _preparar(monkeypatch, hits):
    monkeypatch.setattr(pipeline, "get_connection", _conn_fake)
    monkeypatch.setattr(pipeline, "buscar_tramites", lambda conn, emb, **kw: hits)


def test_caso_claro_streamea_respuesta(monkeypatch):
    _preparar(monkeypatch, [_hit(1, "CEDULA", 0.2), _hit(2, "PASAPORTE", 0.4)])
    deps = _deps()
    cid = deps.store.get_or_create(None)
    eventos = list(procesar_mensaje(deps, cid, "quiero sacar mi carnet"))
    assert ("answer", {"delta": "Hola"}) in eventos
    assert eventos[-1] == ("answer", {"done": True, "tramite_ids": [1]})
    historial = deps.store.mensajes(cid)
    assert historial[-1] == {"role": "assistant", "content": "Hola mundo"}


def test_caso_ambiguo_pregunta_aclaracion(monkeypatch):
    _preparar(monkeypatch, [_hit(1, "A", 0.30), _hit(2, "B", 0.31)])
    deps = _deps()
    cid = deps.store.get_or_create(None)
    eventos = list(procesar_mensaje(deps, cid, "papel"))
    assert eventos == [("clarification", {"text": "¿Te referís a A o B?"})]


def test_sin_resultados_responde_no_encontrado(monkeypatch):
    _preparar(monkeypatch, [])
    deps = _deps()
    cid = deps.store.get_or_create(None)
    eventos = list(procesar_mensaje(deps, cid, "xyzabc"))
    assert eventos[0][0] == "answer"
    assert "No encontré" in eventos[0][1]["delta"]


def test_error_interno_emite_evento_error(monkeypatch):
    monkeypatch.setattr(pipeline, "get_connection", _conn_fake)

    def explota(conn, emb, **kw):
        raise RuntimeError("db caída")

    monkeypatch.setattr(pipeline, "buscar_tramites", explota)
    deps = _deps()
    cid = deps.store.get_or_create(None)
    eventos = list(procesar_mensaje(deps, cid, "hola"))
    assert eventos[-1][0] == "error"


def test_aclaracion_concatena_consulta(monkeypatch):
    _preparar(monkeypatch, [_hit(1, "A", 0.30), _hit(2, "B", 0.31)])
    deps = _deps()
    cid = deps.store.get_or_create(None)
    list(procesar_mensaje(deps, cid, "papel del carro"))
    deps.store.append(cid, "user", "el de propiedad")
    assert deps.store.texto_de_consulta(cid) == "papel del carro el de propiedad"
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `.venv/bin/pytest tests/test_pipeline.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'api.conversations'`.

- [ ] **Step 3: Implementar api/conversations.py**

```python
import uuid


class ConversationStore:
    """Historial en memoria por conversation_id. Se pierde al reiniciar (aceptable para el demo)."""

    def __init__(self):
        self._historiales: dict[str, list[dict]] = {}

    def get_or_create(self, conversation_id: str | None) -> str:
        cid = conversation_id or str(uuid.uuid4())
        self._historiales.setdefault(cid, [])
        return cid

    def append(self, conversation_id: str, role: str, content: str) -> None:
        self._historiales[conversation_id].append({"role": role, "content": content})

    def mensajes(self, conversation_id: str) -> list[dict]:
        return list(self._historiales.get(conversation_id, []))

    def texto_de_consulta(self, conversation_id: str) -> str:
        return " ".join(
            m["content"] for m in self._historiales.get(conversation_id, []) if m["role"] == "user"
        )
```

- [ ] **Step 4: Implementar api/prompts.py**

```python
import json

SISTEMA_FILTROS = """Sos un clasificador de consultas ciudadanas sobre trámites del Estado boliviano.
Dada la consulta, inferí filtros SOLO si son evidentes:
- categoria_slug: slug de la categoría si la consulta claramente pertenece a una.
- evento_vida: nombre del evento de vida si aplica claramente.
- entidad_texto: sigla o nombre de la entidad SOLO si el usuario la menciona (ej. "SEGIP", "aduana").
Ante la duda, devolvé null. Es mejor no filtrar que filtrar mal."""


def schema_filtros(categoria_slugs: list[str], eventos: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "categoria_slug": {"anyOf": [{"type": "string", "enum": categoria_slugs}, {"type": "null"}]},
            "evento_vida": {"anyOf": [{"type": "string", "enum": eventos}, {"type": "null"}]},
            "entidad_texto": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        },
        "required": ["categoria_slug", "evento_vida", "entidad_texto"],
        "additionalProperties": False,
    }


SISTEMA_ACLARACION = """Sos un asistente de trámites del Estado boliviano.
La consulta del ciudadano es ambigua entre varios trámites candidatos.
Formulá UNA sola pregunta breve y clara en español para distinguir cuál necesita.
Mencioná los candidatos por su nombre común (no el nombre técnico completo si es muy largo).
No des información del trámite todavía, solo preguntá."""


def usuario_aclaracion(consulta: str, candidatos: list[dict]) -> str:
    lineas = [f"- {c['nombre']} ({c.get('entidad_nombre') or 'entidad desconocida'})" for c in candidatos]
    return f"Consulta del ciudadano: {consulta}\n\nCandidatos:\n" + "\n".join(lineas)


SISTEMA_SINTESIS = """Sos AMI, asistente de trámites del Estado boliviano. Respondés a ciudadanos en español, de forma clara, breve y accionable.

Reglas estrictas:
- Usá ÚNICAMENTE los datos del trámite provistos en <tramite>. No inventes requisitos, costos, plazos ni oficinas.
- Si un dato que el ciudadano pide no está en <tramite>, decilo explícitamente ("ese dato no figura en la ficha del trámite").
- Si el trámite es virtual y hay URL en modalidades o enlaces, incluila.
- Si costo_es_gratuito es true, aclarar que es gratuito. Si hay costo_monto, dar monto y moneda (UFV = Unidad de Fomento a la Vivienda).
- Respondé la pregunta puntual del ciudadano primero; después agregá lo esencial (requisitos, dónde/cómo, costo).
- Formato: texto corrido con listas cortas si ayudan. Sin encabezados grandes."""


def system_de_sintesis(tramite: dict) -> str:
    datos = {k: v for k, v in tramite.items() if k != "distancia"}
    return SISTEMA_SINTESIS + "\n\n<tramite>\n" + json.dumps(datos, ensure_ascii=False, default=str) + "\n</tramite>"
```

- [ ] **Step 5: Implementar api/pipeline.py**

```python
import logging
from dataclasses import dataclass
from typing import Iterator

from api.confidence import evaluar_confianza
from api.conversations import ConversationStore
from api.prompts import SISTEMA_ACLARACION, SISTEMA_FILTROS, schema_filtros, system_de_sintesis, usuario_aclaracion
from db.connection import get_connection
from db.queries import buscar_entidad_slug, buscar_tramites
from providers.base import ChatProvider, EmbeddingProvider

logger = logging.getLogger(__name__)

MENSAJE_NO_ENCONTRADO = (
    "No encontré un trámite que coincida con tu consulta. ¿Podés reformularla con otras palabras?"
)
MENSAJE_ERROR = "Hubo un problema procesando tu consulta. Intentá de nuevo en un momento."


@dataclass
class Deps:
    chat_economico: ChatProvider
    chat_potente: ChatProvider
    embedder: EmbeddingProvider
    store: ConversationStore
    catalogos: dict


def fetch_live_fallback(consulta: str) -> None:
    # TODO fase "MVP completo" (ver ROADMAP.md): fetch de la página externa vía
    # el campo `enlaces` del registro más cercano + extracción con el modelo potente.
    return None


def inferir_filtros(deps: Deps, consulta: str) -> dict:
    """Infiere filtros con el modelo económico. Fail-open: ante cualquier problema devuelve {}."""
    categorias = deps.catalogos.get("categorias") or []
    eventos = deps.catalogos.get("eventos") or []
    if not categorias or not eventos:
        return {}
    try:
        datos = deps.chat_economico.complete_json(
            system=SISTEMA_FILTROS,
            messages=[{"role": "user", "content": consulta}],
            schema=schema_filtros(categorias, eventos),
            max_tokens=300,
        )
    except Exception:
        logger.warning("inferencia de filtros falló, sigo sin filtros", exc_info=True)
        return {}
    if not datos:
        return {}
    filtros: dict = {}
    if datos.get("categoria_slug"):
        filtros["categoria_slug"] = datos["categoria_slug"]
    if datos.get("evento_vida"):
        filtros["evento_nombre"] = datos["evento_vida"]
    if datos.get("entidad_texto"):
        try:
            with get_connection() as conn:
                slug = buscar_entidad_slug(conn, datos["entidad_texto"])
            if slug:
                filtros["entidad_slug"] = slug
        except Exception:
            logger.warning("búsqueda de entidad falló, sigo sin ese filtro", exc_info=True)
    return filtros


def formular_aclaracion(deps: Deps, consulta: str, candidatos: list[dict]) -> str:
    return deps.chat_economico.complete(
        system=SISTEMA_ACLARACION,
        messages=[{"role": "user", "content": usuario_aclaracion(consulta, candidatos)}],
        max_tokens=300,
    )


def procesar_mensaje(deps: Deps, conversation_id: str, mensaje: str) -> Iterator[tuple[str, dict]]:
    try:
        deps.store.append(conversation_id, "user", mensaje)
        consulta = deps.store.texto_de_consulta(conversation_id)

        filtros = inferir_filtros(deps, consulta)
        embedding = deps.embedder.embed_query(consulta)

        with get_connection() as conn:
            hits = buscar_tramites(conn, embedding, **filtros)
            if not hits and filtros:
                # fail-open: los filtros pueden haber sido mal inferidos
                hits = buscar_tramites(conn, embedding)

        if not hits:
            fetch_live_fallback(consulta)
            deps.store.append(conversation_id, "assistant", MENSAJE_NO_ENCONTRADO)
            yield ("answer", {"delta": MENSAJE_NO_ENCONTRADO})
            yield ("answer", {"done": True, "tramite_ids": []})
            return

        veredicto = evaluar_confianza([h["distancia"] for h in hits])

        if veredicto == "ambiguo":
            pregunta = formular_aclaracion(deps, consulta, hits[:3])
            deps.store.append(conversation_id, "assistant", pregunta)
            yield ("clarification", {"text": pregunta})
            return

        top = hits[0]
        partes: list[str] = []
        for delta in deps.chat_potente.stream(
            system=system_de_sintesis(top),
            messages=deps.store.mensajes(conversation_id),
            max_tokens=4096,
        ):
            partes.append(delta)
            yield ("answer", {"delta": delta})
        deps.store.append(conversation_id, "assistant", "".join(partes))
        yield ("answer", {"done": True, "tramite_ids": [top["id"]]})
    except Exception:
        logger.exception("error procesando mensaje en conversación %s", conversation_id)
        yield ("error", {"message": MENSAJE_ERROR})
```

- [ ] **Step 6: Correr los tests**

Run: `.venv/bin/pytest tests/test_pipeline.py -v`
Expected: 5 passed.

- [ ] **Step 7: Commit**

```bash
git add api/conversations.py api/prompts.py api/pipeline.py tests/test_pipeline.py
git commit -m "feat: pipeline online con filtros, gate, aclaracion y sintesis streaming"
```

---

### Task 8: Endpoint FastAPI POST /chat con SSE

**Files:**
- Create: `api/main.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `procesar_mensaje`, `Deps`, `ConversationStore` (Task 7), `factory` (Task 2), `listar_categorias`/`listar_eventos` (Task 4).
- Produces: app FastAPI `api.main:app` con `POST /chat` (SSE) y `GET /health`. Request: `{"mensaje": str, "conversation_id": str | null}`. Cada `data:` incluye `conversation_id`.

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/test_api.py
from fastapi.testclient import TestClient

import api.main as main
from api.conversations import ConversationStore
from api.pipeline import Deps


def _deps_fake():
    return Deps(chat_economico=None, chat_potente=None, embedder=None, store=ConversationStore(), catalogos={})


def _pipeline_fake(deps, cid, mensaje):
    yield ("answer", {"delta": f"eco: {mensaje}"})
    yield ("answer", {"done": True, "tramite_ids": [42]})


def test_chat_devuelve_sse_con_conversation_id(monkeypatch):
    deps = _deps_fake()
    main.app.dependency_overrides[main.get_deps] = lambda: deps
    monkeypatch.setattr(main, "procesar_mensaje", _pipeline_fake)
    client = TestClient(main.app)

    respuesta = client.post("/chat", json={"mensaje": "hola"})

    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/event-stream")
    cuerpo = respuesta.text
    assert "event: answer" in cuerpo
    assert '"delta": "eco: hola"' in cuerpo
    assert '"conversation_id"' in cuerpo
    assert '"tramite_ids": [42]' in cuerpo
    main.app.dependency_overrides.clear()


def test_health():
    client = TestClient(main.app)
    assert client.get("/health").json() == {"ok": True}
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'api.main'`.

- [ ] **Step 3: Implementar api/main.py**

```python
import json
import logging

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.conversations import ConversationStore
from api.pipeline import Deps, procesar_mensaje
from db.connection import get_connection
from db.queries import listar_categorias, listar_eventos
from providers import factory

load_dotenv()
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="AMI — Asistente de Trámites Bolivia")

_deps: Deps | None = None


def get_deps() -> Deps:
    global _deps
    if _deps is None:
        with get_connection() as conn:
            catalogos = {
                "categorias": [c["slug"] for c in listar_categorias(conn)],
                "eventos": listar_eventos(conn),
            }
        _deps = Deps(
            chat_economico=factory.chat_economico(),
            chat_potente=factory.chat_potente(),
            embedder=factory.embedder(),
            store=ConversationStore(),
            catalogos=catalogos,
        )
    return _deps


class ChatRequest(BaseModel):
    mensaje: str
    conversation_id: str | None = None


def _sse(evento: str, data: dict) -> str:
    return f"event: {evento}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/chat")
def chat(request: ChatRequest, deps: Deps = Depends(get_deps)):
    cid = deps.store.get_or_create(request.conversation_id)

    def generar():
        for evento, data in procesar_mensaje(deps, cid, request.mensaje):
            yield _sse(evento, {"conversation_id": cid, **data})

    return StreamingResponse(generar(), media_type="text/event-stream")
```

- [ ] **Step 4: Correr los tests**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: 2 passed.

- [ ] **Step 5: Prueba manual con datos reales (30 filas cargadas del Task 5)**

Run:
```bash
.venv/bin/uvicorn api.main:app --port 8000 &
sleep 3
curl -s localhost:8000/health
curl -N -s -X POST localhost:8000/chat -H 'content-type: application/json' \
  -d '{"mensaje": "¿cuánto cuesta pedir una copia simple en la aduana?"}' | head -40
kill %1
```
Expected: `{"ok":true}`; luego eventos SSE (`event: answer` con deltas en español o `event: clarification`). Con solo 30 filas cargadas el retrieval puede ser ambiguo — lo importante es que el stream funcione de punta a punta.

- [ ] **Step 6: Commit**

```bash
git add api/main.py tests/test_api.py
git commit -m "feat: endpoint POST /chat con streaming SSE"
```

---

### Task 9: Carga completa + eval de retrieval + calibración de umbrales

**Files:**
- Create: `tests/eval_retrieval.py` (script manual, NO corre con pytest)
- Modify: `api/confidence.py` (solo si la calibración lo indica: ajustar `UMBRAL_GAP` / `UMBRAL_DISTANCIA_MAX`)

**Interfaces:**
- Consumes: `factory.embedder()` (Task 2), `buscar_tramites` (Task 4), `get_connection` (Task 1).
- Produces: script `python tests/eval_retrieval.py` que imprime hit@1, hit@5, distancias top-2 y gap por caso.

- [ ] **Step 1: Carga completa del dataset (requiere NVIDIA_API_KEY; genera 1,739 embeddings + ~23 llamadas al modelo potente)**

Run:
```bash
.venv/bin/python -m ingest.load --jsonl /tmp/tramites.jsonl
docker compose exec db psql -U ami -d ami -c "SELECT count(*), count(embedding), count(*) FILTER (WHERE costo_monto IS NOT NULL) AS con_costo FROM tramites;"
```
Expected: `guardadas 1739/1739` (o el total actual); count = count(embedding); `con_costo` >= 1100.

- [ ] **Step 2: Escribir tests/eval_retrieval.py**

```python
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
CASOS = [
    ("quiero sacar mi carnet", "CEDULA"),
    ("renovar mi carnet de identidad", "CEDULA"),
    ("necesito el papel del carro", "VEHICULO"),
    ("certificado de nacimiento", "NACIMIENTO"),
    ("quiero casarme, qué necesito", "MATRIMONIO"),
    ("sacar el NIT para mi negocio", "NIT"),
    ("licencia de conducir por primera vez", "LICENCIA"),
    ("sacar pasaporte", "PASAPORTE"),
    ("certificado de antecedentes penales", "ANTECEDENTES"),
    ("quiero abrir mi empresa", "EMPRESA"),
    ("bono Juana Azurduy", "JUANA AZURDUY"),
    ("cobrar la renta dignidad", "RENTA DIGNIDAD"),
    ("certificado de soltería", "SOLTER"),
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
```

- [ ] **Step 3: Correr el eval**

Run: `.venv/bin/python tests/eval_retrieval.py`
Expected: tabla con las 19 frases, la mayoría HIT@5. Los MISS son esperables (los substrings son suposiciones sobre nombres reales) — revisar qué devolvió el top-5 y ajustar el substring esperado si el trámite correcto aparece con otro nombre.

- [ ] **Step 4: Calibrar umbrales**

Con la tabla: si muchos HIT@1 claros quedan como "ambiguo" (gap < 0.05), bajar `UMBRAL_GAP`; si consultas sin sentido devuelven "claro", bajar `UMBRAL_DISTANCIA_MAX`. Editar las constantes en `api/confidence.py` y documentar los valores finales en un comentario. Correr `.venv/bin/pytest tests/test_confidence.py -v` — si un cambio de constante rompe un test, ajustar el test al nuevo valor (los tests fijan el CONTRATO del gate, los valores son calibrables).

- [ ] **Step 5: Commit**

```bash
git add tests/eval_retrieval.py api/confidence.py
git commit -m "feat: eval de retrieval con frases reales y calibracion de umbrales"
```

---

### Task 10: README + verificación end-to-end

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: todo lo anterior.
- Produces: documentación de arranque y demo verificada.

- [ ] **Step 1: Escribir README.md**

```markdown
# AMI — Asistente de Trámites Bolivia (demo)

Chat backend que responde preguntas sobre trámites del Estado boliviano
(qué necesito, cuánto cuesta, dónde voy) con RAG sobre el dataset abierto
[tramites-bo](https://github.com/datosbolivia/tramites-bo).

Docs: `CLAUDE.md` (spec original) · `DECISIONS.md` (decisiones) ·
`ROADMAP.md` (qué falta para el MVP completo) ·
`docs/superpowers/specs/` (diseño del demo).

## Requisitos

- Python 3.11+, Docker + docker compose
- API key gratuita de [NVIDIA Build](https://build.nvidia.com) (formato `nvapi-...`)
  — alternativa: `PROVIDER=anthropic` con keys de Anthropic + Voyage

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env    # completar NVIDIA_API_KEY
docker compose up -d    # Postgres+pgvector en localhost:5433
```

## Cargar los datos (una vez, ~3-5 min)

```bash
.venv/bin/python -m ingest.load
```

Descarga los ~1,700 trámites, extrae costos faltantes con el modelo potente
(llama-3.3-70b), genera embeddings (bge-m3) y los guarda en Postgres.

## Correr el API

```bash
.venv/bin/uvicorn api.main:app --port 8000
```

## Usar el chat

```bash
curl -N -X POST localhost:8000/chat -H 'content-type: application/json' \
  -d '{"mensaje": "¿qué necesito para sacar mi carnet de identidad?"}'
```

La respuesta es un stream SSE. Eventos: `answer` (deltas de texto y un
`{"done": true, "tramite_ids": [...]}` final), `clarification` (pregunta
aclaratoria — respondé mandando otro mensaje con el mismo
`conversation_id`), `error`.

Seguimiento de conversación:

```bash
# el primer evento trae conversation_id; usarlo en el siguiente turno
curl -N -X POST localhost:8000/chat -H 'content-type: application/json' \
  -d '{"mensaje": "el de propiedad", "conversation_id": "<uuid>"}'
```

## Tests

```bash
.venv/bin/pytest                                # unit + integración (necesita docker)
.venv/bin/python tests/eval_retrieval.py        # eval de retrieval con frases reales
```
```

- [ ] **Step 2: Verificación end-to-end de los 3 escenarios**

Run (server corriendo con `uvicorn api.main:app --port 8000`):
```bash
# 1. Caso claro: respuesta directa en streaming
curl -N -s -X POST localhost:8000/chat -H 'content-type: application/json' \
  -d '{"mensaje": "¿qué necesito para sacar pasaporte?"}' | tee /tmp/e2e_claro.txt | head -30
# 2. Caso ambiguo + seguimiento (usar el conversation_id que devuelve el paso anterior si fue clarification)
curl -N -s -X POST localhost:8000/chat -H 'content-type: application/json' \
  -d '{"mensaje": "necesito un certificado"}' | tee /tmp/e2e_ambiguo.txt | head -10
# 3. Caso sin match razonable
curl -N -s -X POST localhost:8000/chat -H 'content-type: application/json' \
  -d '{"mensaje": "asdf qwerty xyz"}' | head -10
```
Expected: (1) `event: answer` con texto en español citando requisitos/costo del trámite real; (2) `event: clarification` con una pregunta, o `answer` si el retrieval fue claro — verificar que un segundo mensaje con el mismo `conversation_id` produce una respuesta final; (3) `clarification` o el mensaje "No encontré..." — nunca un stack trace.

- [ ] **Step 3: Suite completa**

Run: `.venv/bin/pytest -v`
Expected: todos los tests pasan.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: README con setup y uso del demo"
```

---

## Notas para el ejecutor

- Los Tasks 1–8 se pueden hacer con `--skip-llm`/30 filas para no gastar cuota de API; la carga completa recién en Task 9.
- El free tier de NVIDIA Build tiene rate limit (~40 req/min): `OpenAICompatEmbeddingProvider` ya reintenta con espera ante errores; si la ingesta completa se corta, re-correrla es seguro (upsert idempotente).
- Si un modelo NIM rechaza `nvext.guided_json`, `complete_json` ya cae solo a prompt+parseo — no tocar código.
- Modelos alternativos por env var sin tocar código: otros chat NIM (`qwen/…`, `deepseek-ai/…`, `nvidia/llama-3.3-nemotron-super-49b-v1.5`) vía `MODELO_*`, o `PROVIDER=anthropic` con keys de Anthropic/Voyage.
- Los umbrales de `api/confidence.py` son valores de partida deliberados; el Task 9 existe para ajustarlos con datos reales.
