# Roadmap — de demo a MVP completo / producto real

Este archivo lista lo que quedó deliberadamente fuera del demo de
hackathon (ver `docs/superpowers/specs/2026-07-13-ami-backbone-demo-design.md`
para el diseño del demo, y `DECISIONS.md` para el porqué de cada
recorte). Después de evaluar el demo cualitativamente, esto es lo que
falta para "MVP completo" y, más adelante, para una "base de producto
real".

## Fase 2 — MVP completo

Implementar el resto del spec original de `CLAUDE.md` tal cual está
descrito:

- **Sync + diff real**: leer `adiciones.csv` y `modificaciones.csv` del
  repo `tramites-bo` y solo upsertear los registros nuevos/modificados,
  en vez de la carga completa única del demo. Correr semanalmente (el
  dataset se actualiza los domingos).
- **Fetch en vivo real** (paso online 4): cuando el retrieval no
  encuentra nada, traer la página externa vía el campo `enlaces` del
  registro más cercano y extraer con el mismo modelo potente y mismo
  esquema de salida que el paso offline 3. Hoy es un stub que responde
  "no encontrado".
- **`tramites_relacionados`**: no hay fuente directa en el dataset. Una
  aproximación posible es agrupar por `eventosVida` compartido, pero
  hay que validar si eso realmente captura adyacencia procedimental
  (siguiente_paso / requisito_previo / alternativa) o solo similitud
  temática.
- **Calibración rigurosa de los umbrales del gate de confianza**: el
  demo arranca con valores de partida sin validar contra un set grande;
  expandir `tests/eval_retrieval.py` con muchas más frases reales
  (idealmente logs de uso real) y ajustar `gap`/distancia absoluta con
  eso.
- **Persistencia real de conversación**: hoy es un dict in-memory que
  se pierde al reiniciar el server. Mover a Redis o a una tabla de
  Postgres si el loop de aclaración necesita sobrevivir reinicios o
  escalar a más de un proceso.
- **Proveedores open-source implementados**: la interfaz
  `ChatProvider`/`EmbeddingProvider` ya está pensada para esto (ver
  `DECISIONS.md` punto 5). Falta la implementación concreta —
  candidatos: Ollama (llama3.1, qwen2.5) para el modelo económico,
  sentence-transformers multilingüe (ej. `intfloat/multilingual-e5-base`)
  para embeddings. Evaluar calidad de retrieval y de síntesis antes de
  migrar el modelo potente.
- **Frontend en TypeScript**: consumir el streaming SSE de `/chat`.
  Puede implicar reestructurar el repo a monorepo (`backend/` +
  `frontend/`) — no se diseñó el backend actual para eso todavía.

## Fase 3 — Base de producto real

Ya marcado como fuera de alcance en el `CLAUDE.md` original, más
detalle operativo:

- Migraciones de DB formales (hoy un solo `schema.sql`) — herramienta
  tipo Alembic si el esquema empieza a cambiar seguido.
- Observabilidad: logging estructurado, métricas de latencia por paso
  del pipeline, tracing de qué modelo/prompt generó cada respuesta.
- Evals de retrieval automatizados en CI (no solo el script manual del
  demo).
- Auth / rate limiting si el API se expone públicamente más allá del
  hackathon.
- Multimodal (voz, imagen, ubicación) como paso de preprocesamiento
  (STT/OCR → texto) antes del pipeline de texto actual — ya
  contemplado en el diseño para no requerir rediseño del core.
- Perfil de usuario / personalización.
- Automatización real de trámites: arquitectura async con colas de
  trabajo, separada del pipeline de consulta síncrono actual.
