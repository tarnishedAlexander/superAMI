# Asistente de Trámites Bolivia — Diseño del demo (backbone de IA)

Fecha: 2026-07-13
Estado: aprobado para pasar a plan de implementación
Fase: demo de hackathon (ver `../../../ROADMAP.md` para lo diferido a
MVP completo / producto real, y `../../../DECISIONS.md` para el registro
completo de preguntas/opciones/elecciones detrás de este diseño)

## Objetivo

Backend de un chat que responde preguntas sobre trámites bolivianos
(qué necesito, cuánto cuesta, dónde voy) usando RAG sobre el dataset
abierto `tramites-bo`, sin perfil de usuario, sin UI, sin multimodal.
Este documento cubre el alcance de la demo: un flujo end-to-end
funcionando en vivo con los datos reales, priorizando la calidad del
paso de síntesis final por sobre la robustez de las ramas secundarias.

## Arquitectura

```
ingest/          script de carga única (jsonl -> Postgres + embeddings)
api/             FastAPI, endpoint POST /chat (SSE streaming)
providers/       ChatProvider (Anthropic) y EmbeddingProvider (Voyage)
db/              schema.sql + queries de retrieval híbrido
docker-compose.yml   Postgres+pgvector local
tests/           eval_retrieval.py + tests unitarios del gate de confianza
```

Repo plano, sin monorepo — la reestructuración para sumar un frontend
TS queda para después de evaluar este demo.

**Flujo end-to-end:** `POST /chat {mensaje, conversation_id?}` → modelo
económico infiere filtros → retrieval híbrido en Postgres (vector +
metadata) → gate de confianza (código puro) → si es claro, modelo
potente sintetiza en streaming SSE; si es ambiguo, modelo económico
pregunta y el backend guarda el estado en memoria por `conversation_id`
para retomar en el siguiente mensaje.

## Modelo de datos (Postgres + pgvector)

```sql
entidades(id, nombre, sitio_web)

tramites(
  id, nombre, sinonimos text[], descripcion,
  entidad_id FK, resultado,
  costo_monto numeric, costo_moneda text, costo_concepto text, costo_es_gratuito bool,
  requisitos jsonb,   -- de modalidades[].publico[].requisitos
  documentos jsonb,   -- top-level documentos[] (nombres)
  ubicaciones jsonb,  -- ubicaciones[] (nombre, tipo, lat/long, dirección)
  modalidades jsonb,  -- crudo completo: tipo, url, horario, forma de cobro
  canal text,         -- 'presencial' | 'virtual' | 'ambos'
  digitalizado bool,  -- esVirtual
  marco_legal text,
  embedding vector(512),  -- voyage-4-lite con output_dimension=512 (ver DECISIONS.md)
  last_updated date
)

categorias(id, nombre) + tramites_categorias(tramite_id, categoria_id)
eventos_de_vida(id, nombre) + tramites_eventos(tramite_id, evento_id)
```

**Mapeo de costo** (hallazgo clave, ver `DECISIONS.md`): el dataset real
ya trae costo estructurado en `modalidades[].publico[].costos` para
1,203 de 1,226 entradas con `tieneCosto=true`. Regla de mapeo:
- `costos[]` no vacío → mapeo directo a `costo_monto`/`costo_moneda`/`costo_concepto`.
- `tieneCosto=false` → `costo_es_gratuito=true`.
- `tieneCosto=true` y `costos` vacío (23 casos) → fallback: modelo
  potente extrae `{monto, moneda, concepto}` de `descripcion`+`resultado`,
  o `null` si no se menciona.

`tramites_relacionados` del `CLAUDE.md` original no tiene fuente clara
en el dataset — queda fuera de esta demo (ver `ROADMAP.md`).

`embedding` se genera de `nombre + descripcion + palabrasClave` (sinónimos).

## Pipeline offline (`ingest/load.py`, corrida única manual)

1. Descargar `tramites.jsonl` del repo `datosbolivia/tramites-bo`.
2. Mapear cada registro a las tablas de arriba (sin modelo).
3. Fallback de costo con modelo potente (Claude Sonnet) SOLO para los
   ~23 registros con `tieneCosto=true` y `costos` vacío. Salida con
   schema JSON fijo vía tool-use de Anthropic.
4. Embeddings en batch (Voyage) de `nombre+descripcion+sinonimos`.
5. Upsert a Postgres por `id`, en lotes de ~100, logueando y saltando
   registros que fallen en vez de abortar todo el batch.

Sin cola de trabajos ni reintentos elaborados — script síncrono de una
sola corrida.

## Pipeline online

**Capa de providers** (`providers/`):
```python
class ChatProvider(Protocol):
    def complete(self, messages, system, tools=None) -> dict: ...
    def stream(self, messages, system) -> Iterator[str]: ...

class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...
```
`AnthropicChatProvider` parametrizada por `model`, instanciada dos veces
(económico=`claude-haiku`, potente=`claude-sonnet`). `VoyageEmbeddingProvider`
implementa `embed`. Sumar un proveedor open-source después (Ollama,
sentence-transformers) es una clase nueva que cumple el protocolo, sin
tocar el resto del pipeline.

**Paso 2 — Retrieval híbrido:**
1. Modelo económico infiere `{categoria_slug?, entidad_slug?, evento_vida_slug?}`
   del mensaje (structured output/tool-use; JSON inválido → se ignora,
   fail-open, nunca bloquea el retrieval).
2. Se embebe el mensaje del usuario (Voyage).
3. SQL: `... WHERE (filtros opcionales) ORDER BY embedding <=> :query LIMIT 5`.
   Si los filtros no devuelven nada, se reintenta sin filtros.

**Paso 3 — Gate de confianza (código puro, sin modelo):** con las 5
distancias del retrieval, `gap = distancia[1] - distancia[0]`. Si el
gap supera un umbral (a calibrar con `tests/eval_retrieval.py`) y la
distancia top-1 es razonablemente baja → claro, se usa top-1
directo. Si no → ambiguo.

**Rama de aclaración:** si es ambiguo, el modelo económico arma una
pregunta sobre los top-3 candidatos, se transmite como evento SSE
`clarification`. El siguiente mensaje del mismo `conversation_id` se
concatena con el mensaje original y se vuelve a correr el paso 2
completo (sin máquina de estados de candidatos pendientes).

**Paso 4 — Fetch en vivo:** stub. `fetch_live_fallback()` devuelve
`None` (TODO apuntando a `ROADMAP.md`). Si el retrieval no devuelve
nada ni sin filtros, se responde "no encontré este trámite, ¿podés
reformularlo?".

**Paso 5 — Síntesis final:** modelo potente recibe el registro
estructurado completo del trámite + la pregunta puntual del usuario,
responde en español en streaming SSE, con instrucción explícita de no
inventar datos fuera de lo provisto.

## API

`POST /chat` — request:
```json
{"mensaje": "necesito el papel del carro", "conversation_id": "uuid opcional"}
```

Respuesta SSE con eventos tipados:
```
event: clarification
data: {"conversation_id": "...", "text": "¿Te referís a la tarjeta de propiedad vehicular o al SOAT?"}

event: answer
data: {"conversation_id": "...", "delta": "Para la tarjeta de..."}
data: {"conversation_id": "...", "done": true, "tramite_ids": [123]}

event: error
data: {"conversation_id": "...", "message": "Hubo un problema, intentá de nuevo."}
```

Si no viene `conversation_id`, el backend genera uno y lo manda en el
primer evento. Historial de conversación en memoria (dict por proceso,
se pierde al reiniciar — aceptable para demo).

## Manejo de errores

- Error de API de Anthropic/Voyage (rate limit, timeout) → se captura,
  se loguea server-side, se emite `event: error` con mensaje genérico;
  nunca se propaga el stack trace al cliente.
- JSON de filtros inválido del modelo económico → fail-open (retrieval
  sin filtros).
- `conversation_id` desconocido en un mensaje de seguimiento → se
  trata como conversación nueva.
- Retrieval sin ningún resultado (ni con ni sin filtros) → rama de
  fetch en vivo (stub) → respuesta "no encontré, reformulá".

## Testing

- `tests/eval_retrieval.py`: ~15-20 frases coloquiales reales
  mapeadas a un `tramite_id` esperado (ej. "papel del carro" → trámite
  de tarjeta de propiedad vehicular). Corre solo el pipeline de
  retrieval (sin el LLM de síntesis), imprime hit/miss. Esto calibra
  los umbrales del gate de confianza del paso 3.
- Tests unitarios con `pytest` para la función pura del gate de
  confianza (distancias falsas, sin DB ni LLM).
- Sin CI configurado para el demo — se corre manualmente.

## Fuera de alcance de esta demo

Ver `ROADMAP.md` para el detalle completo. En resumen: sync+diff real,
fetch en vivo real, `tramites_relacionados`, calibración rigurosa de
umbrales, persistencia real de conversación, proveedores open-source
implementados, frontend, y todo lo que el `CLAUDE.md` original ya
marcaba fuera de alcance (perfil de usuario, multimodal, UI,
automatización real de trámites).
