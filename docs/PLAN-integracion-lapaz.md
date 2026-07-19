# Plan de integración: trámites de lapaz.bo (GAMLP) como 2ª fuente

> **Para el colaborador que ejecuta esto (con su propia Claude Code).**
> Este documento es un spec completo: contexto, hallazgos ya verificados del sitio,
> decisiones tomadas, cambios concretos, verificación y riesgos. Reutilizá lo que ya
> existe en el repo — no reescribas el pipeline.

## Contexto — qué y por qué

El MVP ya está acotado a **impuestos / catastro / actividades económicas** (vista
`dominio_mvp`, ver `DECISIONS.md`). Queremos **sumar** los trámites municipales de
La Paz desde su sitio oficial **https://lapaz.bo/tramites-y-servicios-gamlp/**,
**sin reemplazar** la fuente actual (tramites-bo / gob.bo), para tener fichas más
ricas y actuales del GAMLP.

**Decisiones ya tomadas (no re-discutir):**
1. **Una sola tabla `tramites` + columna `fuente`** (`'gob_bo'` / `'lapaz_gamlp'`).
2. Scrapear **solo el dominio MVP**: impuestos, catastro y territorio, negocios y
   comercio, vehículos.
3. **Mantener duplicados** marcados por `fuente` — NO deduplicar.

## La fuente lapaz.bo (verificado)

- **WordPress.** Cada trámite es un post en `https://lapaz.bo/blog/<slug>/`.
- Archivos de categoría del foco MVP (paginan con `/page/N/`):
  - `https://lapaz.bo/blog/category/impuestos/`
  - `https://lapaz.bo/blog/category/catastro-y-territorio/`
  - `https://lapaz.bo/blog/category/negocios-y-comercio/` (+ subcat `/licencias/`)
  - `https://lapaz.bo/blog/category/vehiculos/`
- `https://lapaz.bo/sitemap.xml` (~900 URLs, plano, sin sub-sitemaps) lista todos los
  `/blog/<slug>/` — sirve para enumerar y contar.
- **Sin API pública:** `https://lapaz.bo/wp-json/wp/v2/...` devuelve **401**. La
  extracción es por **scrape del HTML**.
- Las páginas de detalle están **bien estructuradas**, con secciones consistentes:
  *"En qué consiste"* (descripción), *Requisitos* (separados por **Persona Natural**
  / **Persona Jurídica**), **costo** (a veces literal "no tiene ningún costo"),
  *"Dónde se inicia"* (canal virtual *iGob 24/7* + plataformas físicas con dirección),
  **oficina responsable** (Unidad / Dirección), **tiempo** (ej. "24 a 48 hrs") y
  *"Pasos del ciudadano"*. → Extracción con LLM a JSON es fiable.
- **Solapamiento real con gob.bo:** la GAM La Paz ya existe en el dataset actual
  (ej. "SOLICITUD DE LICENCIAS DE FUNCIONAMIENTO…"). Se mantienen ambos con `fuente`
  distinta (decisión 3).

## Cómo encaja en lo existente (REUTILIZAR, no reescribir)

- `tramites.id` es `integer PRIMARY KEY` provisto por la fuente.
  `guardar_tramite_completo` (`db/queries.py`) hace upsert por `id` recorriendo la
  lista `_COLUMNAS_TRAMITE`, y upserta entidad (por `slug`) y categorías (por `slug`).
  **Agregar una columna a esa lista la hace fluir sola** por el INSERT/UPDATE.
- La vista `dominio_mvp` (`db/schema.sql`) marca in-domain por
  `entidad ILIKE '%municipal%'`. La entidad GAMLP = "Gobierno Autónomo Municipal de
  La Paz" **→ los trámites de La Paz entran al dominio automáticamente, SIN tocar la
  vista.**
- `api/live_fetch.py` usa el campo `enlaces`; si guardás la URL del post lapaz.bo en
  `enlaces`, el fallback "en vivo" ya funciona para estos trámites gratis.
- `ingest/mapper.py::texto_para_embedding` + `factory.embedder().embed_documents`
  se reutilizan tal cual para los embeddings.

## Cambios

### 1. Esquema: columna `fuente` + IDs de La Paz sin colisión (`db/schema.sql`)

```sql
ALTER TABLE tramites ADD COLUMN IF NOT EXISTS fuente text NOT NULL DEFAULT 'gob_bo';

-- gob.bo usa ids 1002–3537; La Paz arranca en 1,000,000 para no colisionar
CREATE SEQUENCE IF NOT EXISTS lapaz_id_seq START 1000000;
CREATE TABLE IF NOT EXISTS lapaz_slug_ids (
  slug text PRIMARY KEY,
  tramite_id integer NOT NULL DEFAULT nextval('lapaz_id_seq')
);
```

Resolver el id de forma **estable e idempotente** por slug (para re-scrapes):

```sql
INSERT INTO lapaz_slug_ids (slug) VALUES (%s)
ON CONFLICT (slug) DO UPDATE SET slug = EXCLUDED.slug
RETURNING tramite_id;
```

### 2. Reconocer `fuente` en el guardado

- `db/queries.py`: agregar `"fuente"` a `_COLUMNAS_TRAMITE`.
- `ingest/mapper.py`: en `mapear_tramite`, agregar `"fuente": "gob_bo"` al dict.

### 3. Nuevo ingest de La Paz (archivos nuevos — espejo de `ingest/load.py`)

**`ingest/lapaz_scrape.py`**
1. **Enumerar** URLs de trámites: crawl de los 4 archivos de categoría MVP con
   paginación (o filtrar `sitemap.xml` por `/blog/` de esas categorías). Cortés:
   `time.sleep` entre requests + cache local del HTML crudo para no re-bajar.
2. Por cada `/blog/<slug>/`: bajar HTML → limpiar a texto/markdown → **extraer con
   `factory.chat_potente()`** vía prompt de extracción dirigida (las secciones
   conocidas → JSON). Usar el patrón fail-open de `complete_json` (nunca lanza).
3. Resolver `id` vía `lapaz_slug_ids`; `fuente='lapaz_gamlp'`; entidad GAMLP
   (slug `gamlp-la-paz`, `nombre` con la palabra "Municipal" para caer en
   `dominio_mvp`); categorías desde la categoría de origen; `enlaces=[{"url": <post>}]`.
4. Embeddings (`texto_para_embedding` + `embed_documents`) y guardar con
   `guardar_tramite_completo` (ambos reuso directo).
5. Flags: `--limit`, `--skip-embeddings`, `--solo-categoria`, y **resumible**
   (saltar slugs ya cargados).

**`ingest/lapaz_mapper.py`** — JSON extraído → dict del esquema `tramites` (misma
forma que `mapear_tramite`, incluida la lógica de costo tipo `mapear_costo`:
gratuito / monto / fallback).

**Prompt de extracción** (en `api/prompts.py` o en el módulo de ingest) — dado el
markdown del post, devolver JSON:
`{nombre, descripcion, requisitos[], costo, ubicaciones[], pasos[], canal}`.

### 4. Relaciones para los nuevos trámites

Correr `python -m ingest.relacionados --solo-sin-relaciones` (flag ya existente)
para conectar los trámites de La Paz al grafo `tramites_relacionados`. Si la capa
gratuita de NIM se bloquea por cuota, clasificar con Claude-en-sesión (como se hizo
el backfill de las 4,089 relaciones actuales).

### 5. (Opcional) Atribución de fuente en la síntesis

Agregar `t.fuente` al SELECT de `_SQL_BUSCAR` (`db/queries.py`) y pasarlo a
`system_de_sintesis` (`api/prompts.py`) para que, ante duplicados gob.bo+lapaz,
prefiera/cite lapaz.bo ("según la página oficial del GAMLP…").

## Verificación (end-to-end)

- Conteo por fuente: `SELECT fuente, count(*) FROM tramites GROUP BY fuente;` →
  `gob_bo` 1739, `lapaz_gamlp` N.
- In-domain automático:
  `SELECT count(*) FROM dominio_mvp d JOIN tramites t ON t.id=d.tramite_id WHERE t.fuente='lapaz_gamlp';`
  **== N** (todos entran por entidad municipal).
- Chat (mismo camino que `POST /chat`; con un `MODELO_POTENTE` que responda hoy,
  ej. `nvidia/nemotron-3-super-120b-a12b`): probar
  *"licencia de funcionamiento en La Paz"*, *"impuesto a la propiedad de inmuebles
  La Paz"*, *"certificado catastral La Paz"* → debe traer la ficha lapaz.bo (más
  rica) y, si hay duplicado, citarla.
- `.venv/bin/pytest` — los 82 tests actuales deben seguir en verde; agregar un test
  del `lapaz_mapper`.
- Re-exportar el dump combinado: `scripts/export_db.sh` → nuevo `ami-AAAAMMDD.dump`
  para compartir (ver `COMPARTIR.md`).

## Riesgos / notas

- **LLM por CADA trámite** (a diferencia de gob.bo, donde solo ~21 lo necesitaban):
  más uso de modelo. Reusar el timeout/retries de `providers/openai_compat.py`,
  hacer el scrape resumible; si NIM se bloquea, extraer con Claude-en-sesión.
- **Duplicados intencionales** gob.bo+lapaz: el gate de confianza puede verlos como
  casi-idénticos (gap chico → veredicto "ambiguo"). Mitigar con la atribución por
  fuente (#5).
- **HTML frágil**: extraer con LLM (no selectores CSS) tolera cambios de markup;
  aislar todo el scraping en `lapaz_scrape.py`.
- **Scraping cortés**: delay + cache; respetar `robots.txt` de lapaz.bo.
- **IDs ≥ 1,000,000**: los FKs `integer` de `tramites_relacionados` /
  `tramites_categorias` / `tramites_eventos` los aguantan sin cambios.
- La Claude Code del colaborador necesita **fetch web** (para el scrape) y una
  **key de LLM** (NIM u otra) para extracción, síntesis y embeddings.
