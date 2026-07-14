# MVP backend — diseño (Fase 2 del ROADMAP, sin frontend)

Fecha: 2026-07-14. Continúa el demo cerrado en
`docs/superpowers/specs/2026-07-13-ami-backbone-demo-design.md` (Tasks 1-10
completas, 38/38 tests). Las decisiones de alcance de esta fase salieron del
brainstorming del 2026-07-14 y se registran acá.

## Alcance decidido

- **Solo backend.** Los 6 ítems de backend de la Fase 2 de `ROADMAP.md`.
  El frontend TypeScript y la posible reestructuración a monorepo quedan
  fuera.
- **Local por ahora, diseñado para migrar.** Sigue corriendo en Docker +
  uvicorn locales, pero ninguna decisión asume local-only (persistencia en
  Postgres, sync como script invocable, sin daemons).
- **NIM sigue siendo el proveedor default.** Los providers open-source
  (Ollama, sentence-transformers) se implementan detrás de la interfaz
  existente con un eval comparativo, sin migrar el default.
- **Criterio de éxito: métricas + gente real.** Eval ampliado con metas
  numéricas, y una sesión final con 3-5 personas cuyas frases retroalimentan
  la calibración.
- **Un solo plan de implementación** con 6 etapas ordenadas (elección
  explícita del usuario sobre la alternativa de sub-proyectos con specs
  separados). Cada etapa cierra con tests verdes como checkpoint.

## Orden de etapas y por qué

1. **Experiencia de respuesta** — el gate conservador es el problema #1
   observado en el E2E del demo; todo lo demás se percibe a través de esa
   experiencia.
2. **Sync + diff real** — mantener la DB fresca sin reprocesar todo.
3. **Fetch en vivo real** — completar el paso online 4 del spec original.
4. **Conversación persistente** — sobrevivir reinicios; prerequisito débil
   para probar con gente real sin perder sesiones.
5. **Trámites relacionados** — valor agregado a la síntesis.
6. **Providers OSS + eval comparativo** — opcionalidad, no migración.

Cierre del MVP: sesión con 3-5 personas reales y re-calibración con sus
frases.

## Etapa 1 — Experiencia de respuesta

### Logging de consultas

Tabla nueva `consultas_log`:

```sql
CREATE TABLE consultas_log (
  id bigserial PRIMARY KEY,
  ts timestamptz NOT NULL DEFAULT now(),
  conversation_id text,
  mensaje text NOT NULL,
  consulta_acumulada text,
  filtros jsonb,
  top_ids integer[],
  top_distancias real[],
  veredicto text,          -- claro | ambiguo | vacio
  respuesta_tipo text      -- answer | clarification | not_found | error
);
```

Se escribe desde `procesar_mensaje` (`api/pipeline.py`) en modo **fail-soft**:
si el INSERT falla, se loguea el warning y la respuesta sale igual. Propósito
doble: debugging y fuente de frases reales para calibración futura (las de la
sesión con personas terminan acá).

### Eval ampliado

`tests/eval_retrieval.py` crece de ~13 a **60-100 frases** etiquetadas en
tres clases:

- **directas**: deben responderse one-shot (etiqueta: tramite_id esperado).
- **ambiguas legítimas**: deben pedir aclaración (etiqueta: conjunto de
  tramite_ids aceptables).
- **no satisfacibles**: no existen en el dataset (cédula, pasaporte,
  licencia de conducir, etc. — los controles negativos ya conocidos); no
  deben inventarse.

Fuentes: frases escritas a mano en lenguaje cotidiano boliviano ("papel del
carro") + variantes generadas con el modelo potente y **curadas a mano**
antes de entrar al set.

### Recalibración del gate + veredicto nuevo "lejano"

El gate actual (`api/confidence.py`) colapsa dos situaciones distintas en
"ambiguo": (a) gap chico entre top-1 y top-2 con d1 razonable — ambigüedad
genuina, corresponde **aclarar**; (b) d1 más allá de `umbral_distancia_max` —
el trámite probablemente **no está en la DB**, y preguntarle al usuario no lo
va a hacer aparecer. Se separa (b) como veredicto nuevo **"lejano"**:

- `ambiguo` (gap chico) → pregunta aclaratoria, como hoy.
- `lejano` (d1 > max) → en la Etapa 1, mensaje honesto de no encontrado; a
  partir de la Etapa 3, dispara el fetch en vivo.

Script de calibración que barre `umbral_gap × umbral_distancia_max` sobre el
eval y reporta, por combinación: % de directas respondidas one-shot, % de
"claro" incorrecto, % de ambiguas que piden aclaración, % de no satisfacibles
gateadas como lejanas. Los umbrales elegidos reemplazan los actuales (GAP
0.03 / DIST 0.52, calibrados con muy pocos puntos). El razonamiento de la
elección se documenta en el mismo comentario del módulo, como ahora.

### Tope de aclaración: máximo 1 ronda

Hoy `procesar_mensaje` puede encadenar aclaraciones sin límite. Nuevo
comportamiento: si la conversación ya tiene una aclaración hecha y el
veredicto vuelve a ser "ambiguo", se responde con el mejor match disponible
siendo explícito sobre la incertidumbre ("te muestro el que mejor coincide…")
y mencionando 2-3 alternativas cercanas (nombre + entidad) para que el
usuario redirija si no era eso.

Requiere que el store de conversación marque el **tipo de cada turno**
(user | clarification | answer | …) — hoy solo guarda role/content. El
cambio de interfaz se hace acá y la persistencia de la Etapa 4 lo hereda.

### Respuesta con match mediocre

Ajustar el prompt de síntesis (`api/prompts.py`) para que cuando el dato
pedido no figura en la ficha, la respuesta: (a) diga qué información sí
tiene el trámite, (b) dirija al enlace oficial del campo `enlaces`, en vez
de responder únicamente "Ese dato no figura en la ficha del trámite". La
regla de no inventar datos se mantiene intacta.

## Etapa 2 — Sync + diff real

Script `ingest/sync.py`, invocable como `python -m ingest.sync`:

1. Descarga `adiciones.csv`, `modificaciones.csv` y `tramites.jsonl` del
   repo `datosbolivia/tramites-bo` (raw URLs de GitHub).
2. Lee la tabla nueva `sync_state` (una fila: fecha de la última corrida
   procesada). Filtra las filas de los CSVs posteriores a esa fecha.
3. Upsertea **solo esos IDs** desde el jsonl: re-mapea (mapper existente),
   re-embebe, y corre extracción de costo LLM únicamente donde
   `tieneCosto=true` y `costos` vacío (el fallback de ~1.2% ya conocido).
4. **Bajas**: trámites que desaparecen se marcan `activo = false` (columna
   nueva en `tramites`, default true) — no se borran, conservan historial y
   FKs. `buscar_tramites` filtra `activo = true`.
5. Actualiza `sync_state` al final. Idempotente: correrlo dos veces seguidas
   no repite trabajo.

Tolerancia por registro: un registro que falla al mapear/embeber se loguea y
no aborta la corrida. Sin scheduler embebido — se documenta en el README cómo
agendarlo (cron / Task Scheduler), coherente con "local, diseñado para
migrar".

**Supuesto a verificar al implementar** (documentado, no bloquea el diseño):
el formato exacto de columnas de `adiciones.csv` y `modificaciones.csv`
(fecha de corrida, id del trámite, tipo de cambio). Si los CSVs no traen lo
necesario para el diff, el fallback es comparar `fechaActualización` del
jsonl contra `last_updated` de la tabla — mismo efecto, un poco más de I/O.

## Etapa 3 — Fetch en vivo real

Reemplaza el stub `fetch_live_fallback` de `api/pipeline.py`.

- **Trigger**: veredicto `vacio` (sin hits) o `lejano` (el veredicto nuevo
  de la Etapa 1: mejor match más allá de `umbral_distancia_max`).
- **Flujo**: tomar el registro más cercano con `enlaces` no vacío → fetch de
  la página (httpx, timeout corto ~10s, límite de tamaño de respuesta) →
  extraer el texto principal del HTML → extracción con el **mismo modelo
  potente y mismo esquema de salida** que el paso offline (como manda el
  spec original de `CLAUDE.md`) → síntesis marcando la fuente ("según la
  página oficial de X, que puede estar desactualizada").
- **Caché**: tabla `fetch_cache` (url PK, contenido extraído jsonb,
  fetched_at) con TTL (~7 días, alineado al ciclo semanal del dataset) para
  no re-fetchear la misma página en cada consulta.
- **Fail-soft total**: timeout, HTML inservible o extracción vacía degradan
  al mensaje actual de "no encontré" — nunca un error al usuario.

## Etapa 4 — Conversación persistente

- Tabla `conversaciones` (id text PK, mensajes jsonb — incluye el tipo de
  turno introducido en la Etapa 1 —, created_at, updated_at).
- `PostgresConversationStore` implementa la **misma interfaz** que el
  `ConversationStore` in-memory actual (`get_or_create`, `append`,
  `mensajes`, `texto_de_consulta`); el pipeline no cambia. El store
  in-memory queda para tests.
- **Postgres sobre Redis**: ya está en el stack, cero infra nueva, sobrevive
  reinicios, migra a hosteado sin cambios.
- Limpieza de conversaciones viejas (updated_at > 24h) al arranque del
  server y/o en la corrida de sync — sin daemon aparte.

## Etapa 5 — Trámites relacionados

- Tabla `tramites_relacionados` (tramite_id, related_tramite_id,
  tipo_relacion: `siguiente_paso | requisito_previo | alternativa |
  mismo_evento`), la del spec original.
- **Generación en dos pasos** para acotar costo:
  1. Candidatos por heurística barata: mismo evento de vida o misma entidad,
     rankeados por cercanía de embedding; top-5 por trámite.
  2. El modelo potente clasifica los 5 candidatos de cada trámite **en una
     sola llamada** (tipo de relación o "ninguna"). ~1,700 llamadas offline,
     corre una vez; respeta el rate limit de NIM con los mismos reintentos
     del pipeline de embeddings.
- **Validación previa obligatoria**: script que clasifica una muestra de ~20
  trámites y la imprime para revisión manual. El ROADMAP duda con razón de
  si `eventosVida` compartido captura adyacencia procedimental — esto lo
  responde con datos antes de gastar las 1,700 llamadas. Si la muestra sale
  mala, se ajusta la heurística de candidatos antes de correr todo.
- **Uso online**: la síntesis recibe hasta 2-3 relacionados en el system
  prompt para anticipar necesidades ("para esto también vas a necesitar…").

## Etapa 6 — Providers OSS + eval comparativo

NIM sigue siendo default; esto agrega opcionalidad, no migración.

- **Ollama (LLMs)**: Ollama expone API OpenAI-compatible, así que en
  principio es el `OpenAICompatChatProvider` existente apuntando a
  `http://localhost:11434/v1` vía factory (`PROVIDER=ollama`), sin código
  nuevo. `nvext.guided_json` no existe en Ollama; el fallback de
  prompt + parseo tolerante ya implementado cubre el JSON estructurado.
  Modelos candidatos: llama3.1, qwen2.5 (los del ROADMAP).
- **Embeddings locales**: `SentenceTransformersEmbeddingProvider` con
  `intfloat/multilingual-e5-base` (768 dims). Como la dimensión difiere de
  los 1024 de la DB, el **eval comparativo corre offline sin tocar la DB**:
  embebe corpus (nombre+descripcion+sinonimos de los 1,739) y frases del
  eval en memoria, calcula hit@k y métricas del gate por backend, reporta
  comparación. Migrar la DB (re-embed + ALTER de dimensión) sería una
  decisión posterior, solo si gana con claridad.
- **Comparación de síntesis**: mismas frases del eval con NIM vs Ollama,
  salida lado a lado en markdown para juicio cualitativo.
- `sentence-transformers` (y torch) entran como dependencia **opcional**
  (extra de requirements separado) para no engordar el install base.

## Criterios de éxito del MVP

Medidos sobre el eval ampliado, después de recalibrar:

- hit@5 ≥ 90% en consultas satisfacibles.
- **Cero "claro" incorrecto** — nunca responder con confianza el trámite
  equivocado.
- ≤ 25% de las consultas directas caen en aclaración innecesaria (en el
  demo es la mayoría).
- 100% de las no satisfacibles sin inventar datos.
- Sesión final con 3-5 personas reales: sus frases entran a `consultas_log`,
  se re-corre la calibración y se verifica que las metas se sostienen.

## Testing y manejo de errores

- TDD como en el demo: unit tests con mocks de LLM/embeddings; integración
  con Docker Postgres (localhost:5433); el eval sigue siendo script manual
  (automatizarlo en CI es Fase 3).
- Patrones existentes se mantienen: **fail-open** en filtros (error del
  modelo económico ⇒ seguir sin filtros), **fail-soft** en logging y fetch
  (error ⇒ degradar, nunca romper la respuesta).
- Sync tolerante por registro; errores acumulados se reportan al final de la
  corrida.

## Fuera de alcance (se mantiene en ROADMAP)

- Frontend TypeScript / monorepo.
- Deploy hosteado, auth, rate limiting.
- Migraciones formales de DB (los cambios de esquema de esta fase entran a
  `db/schema.sql` con `IF NOT EXISTS` / `ALTER` idempotentes documentados).
- Evals en CI, observabilidad estructurada, multimodal, perfil de usuario,
  automatización de trámites.
