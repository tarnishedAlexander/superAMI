# Asistente de Trámites Bolivia — Especificación del backbone de IA

## Contexto

Portal gob.bo: 646 entidades, 1,250 trámites, 15 categorías, 8 eventos de vida,
274 trámites digitalizados. Objetivo de esta fase: un chat que responde
preguntas sobre trámites (qué necesito, cuánto cuesta, dónde voy) usando RAG,
sin perfil de usuario, sin UI (backend/API únicamente), sin multimodal por
ahora. La automatización real de trámites (fase futura) queda fuera de este
alcance.

## Fuente de datos

En lugar de scrapear gob.bo directamente, usar el dataset abierto ya
mantenido:

- Repo: https://github.com/datosbolivia/tramites-bo (fork de
  mauforonda/tramites-bo)
- Archivo principal: `tramites.jsonl` — un JSON por línea, un trámite por
  registro
- Esquema declarado en `datapackage.json`: id, estado, fechaActualización,
  nombre, slug, sigla, descripcion, marcoLegal, palabrasClave, like, dislike,
  esPresencial, esVirtual, entidad (objeto), resultado, enlaces, categorias,
  eventosVida, herramientas, documentos, modalidades, ubicaciones
- Licencia CC0 — libre de usar
- Se actualiza cada domingo. El repo también publica `adiciones.csv`
  (trámites que aparecen/desaparecen entre corridas) y `modificaciones.csv`
  (trámites que cambian entre corridas) — usar estos para sync incremental,
  no reprocesar los 1,250 registros cada semana.
- Nota: el esquema NO tiene un campo `costo` estructurado. Si el costo
  existe, probablemente está embebido en `descripcion` o `resultado` como
  texto libre.

## Esquema de base de datos (Postgres + pgvector)

Tablas principales:

- `entidades` (id, nombre, sitio_web)
- `tramites` (id, nombre, sinonimos[], descripcion, entidad_id FK, costo,
  requisitos jsonb, documentos jsonb, ubicaciones jsonb, canal, digitalizado
  bool, embedding vector, last_updated)
- `categorias` (id, nombre) + tabla puente `tramites_categorias`
- `eventos_de_vida` (id, nombre, descripcion) + tabla puente
  `tramites_eventos`
- `tramites_relacionados` (tramite_id, related_tramite_id, tipo_relacion:
  siguiente_paso | requisito_previo | alternativa | mismo_evento) — para
  anticipar necesidades relacionadas sin depender de similitud de embeddings
  (que no captura adyacencia procedimental, solo similitud semántica de
  texto)

`sinonimos` se llena desde `palabrasClave` del dataset, más enriquecimiento
opcional (variantes de lenguaje cotidiano). Este campo se concatena al texto
que se embebe, junto con nombre y descripción, para cerrar la brecha entre
cómo habla el ciudadano y el nombre formal del trámite.

## Pipeline offline (una vez por semana)

1. **Sync + diff** (código, sin modelo): pull de `tramites.jsonl` +
   `adiciones.csv` + `modificaciones.csv`. Solo procesar registros nuevos o
   modificados.
2. **Mapeo a esquema** (código, sin modelo): transformar el JSON del dataset
   directamente a las columnas de la tabla `tramites`. La mayoría de campos
   ya vienen estructurados — no requiere modelo.
3. **Extracción dirigida** (LLM, modelo potente — "modelo A"): SOLO para
   campos que el dataset no estructura, principalmente `costo`. Prompt
   acotado: dado `descripcion` + `resultado`, extraer costo si existe.
   Correr solo sobre registros marcados como nuevos/modificados por el diff.
4. **Embeddings + almacenamiento**: generar embedding de
   nombre+descripcion+sinonimos, guardar junto con los campos estructurados.

## Pipeline online (por cada consulta del chat)

1. **Consulta del usuario** — mensaje entrante, sin contexto de perfil.
2. **Retrieval híbrido** (LLM económico — "modelo B" — + búsqueda vectorial):
   modelo económico infiere filtros de categoria/entidad/evento_de_vida del
   texto libre si es posible; búsqueda vectorial + filtros de metadata sobre
   la tabla `tramites`.
3. **Chequeo de confianza** (código, sin modelo): umbral numérico sobre
   similitud del top resultado vs. el segundo. Sin modelo — puramente un
   gate determinista.
   - Si es claro → generar respuesta directo.
   - Si es ambiguo → modelo económico formula una pregunta aclaratoria,
     vuelve a retrieval con la respuesta del usuario.
4. **Si no está en la DB**: fetch en vivo de la página externa vía el campo
   `enlaces` del registro más cercano (código, sin modelo) → extracción con
   el mismo modelo potente y mismo prompt que el paso offline 3 (mismo
   esquema de salida).
5. **Generar y transmitir respuesta** (LLM potente — "modelo A" o
   equivalente): dado el registro estructurado + la pregunta específica del
   usuario, sintetizar una respuesta conversacional en streaming. Este es el
   único paso que corre en TODAS las consultas y donde la calidad importa
   más — no escatimar aquí.

## Asignación de modelos

- **Modelo económico**: inferencia de filtros (paso 2), formular pregunta
  aclaratoria (paso 3, rama ambigua). Tareas acotadas, de bajo riesgo si
  imperfectas, corren en cada consulta.
- **Modelo potente**: extracción (offline paso 3 + fallback en vivo paso 4)
  y síntesis final (paso 5). Los errores de extracción se persisten en la DB
  y se sirven a todos los usuarios futuros — vale la pena invertir aquí
  aunque corra pocas veces. La síntesis es lo que el ciudadano realmente lee
  — la barra de precisión es la más alta de todo el sistema.

## Fuera de alcance en esta fase

- Perfil de usuario / personalización
- Multimodal (voz, imagen, ubicación) — diseñar el pipeline de texto de
  forma que esto se pueda añadir después como un paso de preprocesamiento
  (STT/OCR → texto) sin rediseñar el core
- UI/UX — enfoque es backend/API únicamente
- Automatización real de trámites (fase futura, arquitectura async con
  colas de trabajo, separada de este pipeline de consulta síncrono)

## Orden de construcción sugerido

1. Script de sync + diff contra tramites-bo
2. Mapeo de esquema + carga a Postgres
3. Embeddings + índice pgvector
4. Prompt de extracción dirigida (costo) + prompt de síntesis final
5. Endpoint de retrieval híbrido + chequeo de confianza (umbral)
6. Rama de aclaración (loop) + rama de fetch externo
7. Pruebas con frases reales de usuarios ("papel del carro" en vez de
   "tarjeta de circulación", etc.) — la calidad del retrieval depende de
   esto más que de cualquier otra decisión de arquitectura
