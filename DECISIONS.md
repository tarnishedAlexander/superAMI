# Decisiones de diseño — fase demo (hackathon)

Registro de las preguntas hechas durante el brainstorming, las opciones
presentadas y qué se eligió. Sirve como referencia al pasar de este
prototipo a la fase "MVP completo" (ver `ROADMAP.md`): qué se decidió,
por qué, y qué vale la pena reconsiderar con más tiempo/datos.

Fecha: 2026-07-13.

## 1. Horizonte del proyecto

**Pregunta:** ¿Demo de hackathon, MVP completo, o base de producto real?

**Opciones:** (a) demo de horas/1-2 días con ramas secundarias en stub,
(b) MVP completo implementando todo el spec de `CLAUDE.md`, (c) además
del MVP, cuidar migraciones/observabilidad/evals para producto real.

**Elegido:** (a) Demo de hackathon. El usuario evaluará cualitativamente
el resultado y luego decide si sigue con MVP completo y/o producto real.
Por eso este registro y `ROADMAP.md` existen — para no perder el
contexto de qué quedó simplificado a propósito.

## 2. Lenguaje / stack de backend

**Contexto:** se consideró si el lenguaje debía anticipar un frontend.

**Elegido:** Python + FastAPI para el backend ahora. Frontend en
TypeScript se evaluará más adelante, y el proyecto se reestructurará
quizás como monorepo en ese momento — no se diseña para eso todavía
(YAGNI).

## 3. Proveedor de LLM (modelo económico + modelo potente)

**Pregunta:** ¿Anthropic, OpenAI, o mixto/lo más barato?

**Contexto adicional del usuario:** quiere poder usar tanto modelos
cerrados como open-source, así que se abre una pregunta de arquitectura
sobre cómo abstraer proveedores (ver punto 5).

**Elegido para el par concreto del demo:** Anthropic para ambos —
Claude Sonnet como modelo potente (extracción + síntesis), Claude Haiku
como modelo económico (filtros + pregunta aclaratoria). Se descartó la
opción de mezclar Anthropic + Ollama local para el demo (mayor fricción
de setup) y la de todo-open-source (riesgo de calidad en la síntesis,
que el spec marca como el paso más exigente).

## 4. Embeddings

**Pregunta:** Anthropic no tiene API de embeddings propia (recomienda
Voyage AI como partner). ¿Voyage, OpenAI text-embedding-3-small, o
sentence-transformers local?

**Elegido:** Voyage AI ahora. El usuario quiere un provider también acá
(mismo patrón que los LLMs) para poder migrar a opciones open-source
más adelante, después de evaluar la calidad del demo.

## 5. Capa de abstracción de providers (LLM + embeddings)

**Pregunta:** ¿Adaptadores propios minimalistas, LiteLLM, o sin
abstracción por ahora?

**Elegido:** Adaptadores propios (protocolo mínimo `ChatProvider` /
`EmbeddingProvider`, un método cada uno). Se descartó LiteLLM (dependencia
extra, oculta detalles específicos de Anthropic que sí importan acá) y
"sin abstracción" (contradice el pedido explícito de poder sumar
open-source después sin tocar cada call site).

**Por qué importa para el futuro:** cuando se evalúe mover el modelo
económico y/o embeddings a algo open-source (Ollama, sentence-transformers),
la extensión es una clase nueva que implementa el protocolo, no un
refactor del core.

## 6. Postgres

**Pregunta:** ¿Docker local, Supabase, o Neon?

**Elegido:** Docker local con imagen `pgvector/pgvector:pg16`. Cero
fricción de cuentas externas para el hackathon; migra fácil a un
Postgres hosteado después (mismo esquema SQL).

## 7. Alcance del "fetch en vivo" (paso online 4 del spec)

**Pregunta:** ¿Implementar completo el fallback de fetch a la página
externa cuando el trámite no está en la DB, o dejarlo en stub?

**Elegido:** Stub / fuera de alcance para el demo. Con 1,739 trámites
cargados el caso "no está en la DB" es raro. El hook queda en el código
(función que hoy responde "no encontrado, reformulá tu pregunta") — se
implementa completo en la fase MVP completo (ver `ROADMAP.md`).

## 8. Sync + diff (paso offline 1 del spec)

**Pregunta:** ¿Carga completa única, o implementar ya la lógica de diff
real leyendo `adiciones.csv`/`modificaciones.csv`?

**Elegido:** Carga completa única (upsert de los 1,739 registros
actuales). La tabla queda con `last_updated` para soportar diff después,
pero el demo no lee los CSVs de diff — eso pasa a `ROADMAP.md`.

## 9. Forma del API

**Pregunta:** ¿`POST /chat` con streaming SSE, o sin streaming?

**Elegido:** `POST /chat` con streaming SSE. El spec marca la síntesis
final como el paso de mayor calidad percibida — sin streaming se pierde
ese efecto. Fácil de consumir después desde un frontend TS.

## 10. Estado de conversación (para el loop de aclaración)

**Pregunta:** ¿Historial en memoria por `conversation_id`, o el cliente
reenvía todo el historial en cada request?

**Elegido:** Historial en memoria por `conversation_id` (dict en el
proceso del backend, se pierde al reiniciar — aceptable para demo).
Alcanza para sostener el loop de aclaración multi-turno sin necesitar
que el cliente lleve la cuenta.

## 11. Archivo separado para el roadmap de "MVP completo"

**Pregunta:** ¿`ROADMAP.md` en la raíz del proyecto, o un segundo spec
formal en `docs/superpowers/specs/`?

**Elegido:** `ROADMAP.md` en la raíz, junto a `CLAUDE.md` y este archivo.

## Hallazgo de datos que cambió un supuesto del spec

El `CLAUDE.md` original asume que el dataset **no** tiene un campo de
costo estructurado ("probablemente está embebido en `descripcion` o
`resultado` como texto libre"). Al inspeccionar `tramites.jsonl` real
(1,739 registros, descargado 2026-07-13):

- Cada entrada de `modalidades[].publico[]` ya trae `tieneCosto` (bool)
  y `costos: [{conceptoPago, costo, moneda}]` estructurado.
- De 1,226 entradas con `tieneCosto=true`, **1,203 ya tienen `costos`
  lleno** — solo 23 tienen `tieneCosto=true` pero `costos` vacío (el
  caso que sí necesitaría el fallback de extracción con el modelo
  potente).
- Monedas observadas: `Bs`, `USD`, `UFV`.

**Impacto en el diseño:** el paso offline 3 ("extracción dirigida con
modelo potente") deja de ser el camino principal para costo — se vuelve
un fallback de baja frecuencia (~1.2% de las entradas con costo). El
mapeo directo de esquema (paso offline 2, sin modelo) cubre casi todo.
Esto no estaba decidido por el usuario, es una corrección de hecho
sobre el spec — vale la pena revalidarlo si el dataset cambia
sustancialmente en el futuro (se actualiza cada domingo).

**Otro ajuste de hecho al esquema:** el campo `herramientas` del
dataset original (mencionado en `CLAUDE.md`) está vacío en las 1,739
entradas actuales (`0/1739`). Se omite de la tabla `tramites` en el
demo — trivial de re-agregar si el dataset empieza a poblarlo.

**`eventosVida` es una lista de strings** ("Empleo"), no objetos
`{nombre, slug}` como `categorias`. La tabla `eventos_de_vida` se keyea
por `nombre` (UNIQUE) en vez de slug.

## Ajustes hechos al escribir el plan de implementación (2026-07-13)

- **IDs de modelo concretos** (verificados contra la referencia actual
  de la API de Anthropic): potente = `claude-sonnet-5`, económico =
  `claude-haiku-4-5`. No se pasa `temperature`/`top_p`/`top_k` (Sonnet 5
  rechaza valores no default con 400).
- **Structured output**: se usa `output_config.format` (json_schema),
  el mecanismo vigente recomendado, en vez de tool-use con `tool_choice`
  forzado.
- **Embeddings**: Voyage ya recomienda la familia `voyage-4`; el spec
  mencionaba `voyage-3-lite`. Se usa **`voyage-4-lite` con
  `output_dimension=512`** — misma dimensión que el spec (`vector(512)`),
  mejor calidad, y `voyage-3.5-lite` @ 512 queda como fallback por env
  var si hiciera falta.

## Cambio de proveedor: NVIDIA NIM (2026-07-13, durante la ejecución)

**Contexto:** al arrancar la ejecución el usuario no tenía API key de
Anthropic ni de Voyage (Claude Pro es la suscripción del chat, no da
acceso a la API). Propuso usar las keys gratuitas de
[NVIDIA Build](https://build.nvidia.com) (NIM, API OpenAI-compatible en
`https://integrate.api.nvidia.com/v1`).

**Decisión:** proveedor primario = NVIDIA NIM. La capa de providers se
diseñó exactamente para esto (punto 5): se agregó
`OpenAICompatChatProvider`/`OpenAICompatEmbeddingProvider` y el factory
elige por env `PROVIDER` (default `nvidia`). El código de
Anthropic/Voyage queda en el repo como alternativa (`PROVIDER=anthropic`)
para cuando haya keys.

**Modelos elegidos (overrideables por env):**
- Potente: `meta/llama-3.3-70b-instruct` — español sólido, buen
  instruction-following para "no inventar datos", rápido en NIM.
- Económico: `meta/llama-3.1-8b-instruct` — filtros/aclaración con
  diseño fail-open que tolera sus errores.
- Embeddings: `baai/bge-m3` — el multilingüe de mejor reputación del
  catálogo NIM para retrieval en español. **1024 dims** → el esquema
  pasó de `vector(512)` a `vector(1024)` (DB recreada, aún sin datos).

**Detalles técnicos:** JSON estructurado vía `nvext.guided_json` (vLLM)
con fallback a prompt+parseo tolerante; `complete_json` es fail-open.
Sin parámetros de sampling (defaults del servidor). Free tier ~40
req/min: embeddings en lotes de 32 con reintentos.

**Para reevaluar en el MVP:** si aparecen keys de Anthropic, comparar
calidad de síntesis (`PROVIDER=anthropic`) — la síntesis es el paso con
la barra de calidad más alta del spec; y considerar modelos NIM más
nuevos (deepseek-v4, qwen3.5, nemotron) para el rol potente.
