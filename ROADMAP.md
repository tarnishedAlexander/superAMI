# Roadmap — de demo a MVP completo / producto real

Este archivo lista lo que quedó deliberadamente fuera del demo de
hackathon (ver `docs/superpowers/specs/2026-07-13-ami-backbone-demo-design.md`
para el diseño del demo, y `DECISIONS.md` para el porqué de cada
recorte). Después de evaluar el demo cualitativamente, esto es lo que
falta para "MVP completo" y, más adelante, para una "base de producto
real".

## Fase 2 — MVP completo

Implementar el resto del spec original de `CLAUDE.md` tal cual está
descrito. Estado tras el plan de implementación
(`docs/superpowers/plans/2026-07-14-mvp-backend.md` /
`docs/superpowers/specs/2026-07-14-mvp-backend-design.md`), Tasks 1-18:

- **Sync + diff real**: ✔ implementado — `ingest/sync.py` (Task 9),
  diff por `fechaActualización` vs `last_updated`, con `--dry-run` y
  marcado de bajas (`activo=false`). Ver `DECISIONS.md` para el porqué
  de comparar por fecha en vez de parsear los CSVs de terceros.
- **Fetch en vivo real** (paso online 4): ✔ implementado —
  `api/live_fetch.py` (Task 10-11), con caché de 7 días
  (`fetch_cache`) y fail-soft total. Reemplaza el stub del demo.
- **`tramites_relacionados`**: ✔ implementado — `ingest/relacionados.py`
  (Task 13-15), clasificación con el modelo potente sobre candidatos
  por misma entidad/evento, validada a mano con muestra e integrada en
  la síntesis final. Corrida batch completa sobre los 1,739 trámites.
- **Calibración rigurosa de los umbrales del gate de confianza**: ✔
  proceso implementado — eval ampliado a 97 casos (Task 6) y script de
  barrido `tests/calibrar_gate.py` (Task 7). **Hallazgo**: con el
  dataset completo no existe una combinación de umbrales que cumpla los
  4 criterios de éxito del spec sin anular las respuestas directas
  (ver `DECISIONS.md`, secciones "Task 7" y "Verificación final del MVP
  backend") — decisión deliberada de no comprometer el gate para
  compensar un problema de calidad de retrieval/dataset. Pendiente de
  la sesión con usuarios reales (`docs/guia-sesion-usuarios.md`).
- **Persistencia real de conversación**: ✔ implementado —
  `PostgresConversationStore` (Task 12), tabla `conversaciones`, con
  limpieza best-effort de conversaciones >24h.
- **Proveedores open-source implementados**: ✔ implementado —
  `PROVIDER=ollama` + `SentenceTransformersEmbeddingProvider` (Task 16).
  La **migración** de embeddings quedó **evaluada pero no ejecutada**:
  eval comparativo corrido (Task 17, resultado en `DECISIONS.md`),
  `PROVIDER=nvidia` sigue siendo el default sin alteraciones — la
  decisión de migrar se difiere a después del MVP.
- **Frontend en TypeScript**: sigue **pendiente**. Consumir el
  streaming SSE de `/chat`. Puede implicar reestructurar el repo a
  monorepo (`backend/` + `frontend/`) — no se diseñó el backend actual
  para eso todavía.

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
