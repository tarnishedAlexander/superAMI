# Entrega / Compartir — AMI acotado al dominio MVP

Guía para poner a correr AMI **sin reprocesar nada**: los embeddings y el grafo
de relaciones entre trámites ya están calculados y viajan dentro de un dump de
la base de datos. Tus compañeros solo importan el dump y levantan el API.

> El procesamiento pesado (embeddings de 1,739 trámites + clasificación de
> ~4,000 relaciones procedimentales) ya está hecho. En una máquina limpio tomó
> **días**; con el dump toma **~2 minutos**.

---

## 1. Qué cambió — nuevo foco del MVP

El MVP se **acotó a 3 dominios**: **impuestos, catastro y actividades
económicas**. Antes el asistente respondía sobre las 17 categorías del dataset;
ahora solo recupera trámites dentro de ese alcance.

Hallazgo al mapear contra los datos reales: el dataset **no tiene** una
categoría "catastro" ni "actividades económicas". Por eso el alcance se define
con un **predicado híbrido** (vista SQL `dominio_mvp`), no con una simple lista
de categorías:

Un trámite entra al dominio MVP si cumple **cualquiera** de estas condiciones:

| Condición | Cómo se detecta | Trámites |
|-----------|-----------------|----------|
| Categoría | `categoría IN (impuestos, empresas)` | 377 |
| Entidad municipal | `entidad ILIKE '%municipal%'` (9 GAMs, RUAT, etc.) | 227 |
| Catastro | palabra clave `catastr%` en nombre/descripción/sinónimos | 21 |

**Total: 511 de 1,739 trámites** quedan dentro del alcance (con solapes). Cada
fila de la vista lleva columnas `por_categoria`, `por_entidad`, `por_keyword`
que explican *por qué* pertenece — es auditable con un `SELECT`.

---

## 2. Cambios técnicos (qué archivos se tocaron)

Refinamiento #1 del scope + el relleno del grafo de relaciones + parches de
resiliencia. Todo está en el diff actual de la rama `main` (sin commitear aún):

- **`db/schema.sql`** — vista `dominio_mvp` (el predicado híbrido de arriba).
- **`db/queries.py`** —
  - `_SQL_BUSCAR`: filtro *always-on* que restringe la búsqueda vectorial al
    dominio (`EXISTS (SELECT 1 FROM dominio_mvp d WHERE d.tramite_id = t.id)`).
  - `listar_categorias_dominio()`: el enum de categorías inferibles se reduce a
    las que tienen ≥1 trámite en el dominio.
  - `candidatos_relacionados` / `listar_relacionados`: **sin** acotar a
    propósito (un trámite municipal puede requerir uno nacional como CI o NIT).
- **`api/main.py`** — `get_deps` usa `listar_categorias_dominio`.
- **`api/pipeline.py`** — `MENSAJE_NO_ENCONTRADO` aclara el alcance actual.
- **`api/prompts.py`** — reglas de "fuera de alcance" en la síntesis.
- **`tests/test_queries.py`** — fixtures ajustadas + test de exclusión de dominio.
- **`ingest/relacionados.py`** — flags `--hasta` (rangos paralelos),
  `--solo-sin-relaciones` (backfill) y `PAUSA_SEGUNDOS` por env.
- **`providers/openai_compat.py`** — cliente OpenAI con `timeout=60`,
  `max_retries=3`, y gate `SALTAR_GUIDED_JSON=1` (para modelos que ignoran el
  guided decoding y duplican llamadas).
- **`scripts/export_db.sh` / `scripts/import_db.sh`** — exportar/importar el dump.

Los 82 tests pasan. Detalle de decisiones en `DECISIONS.md`.

---

## 3. El grafo de relaciones (lo que "procesamos antes")

La tabla **`tramites_relacionados`** es el trabajo pesado que se compartió:
para cada trámite, un modelo clasificó su relación procedimental con los
candidatos cercanos (por entidad/evento compartido + cercanía de embedding) en
uno de cuatro tipos. Es lo que permite anticipar "después de esto probablemente
necesités…", algo que la sola similitud de embeddings no captura.

Estado final (dentro del dump):

| | |
|---|---|
| Trámites activos con embedding | **1,739** |
| Relaciones totales | **4,089** |
| Trámites con ≥1 relación | **1,567** (90 %) |
| Trámites sin relación (ceros legítimos) | 172 |

Distribución por tipo: `alternativa` 2,177 · `mismo_evento` 862 ·
`siguiente_paso` 572 · `requisito_previo` 478.

**Todo esto está dentro de `ami-20260719.dump`** — no hay que volver a
calcularlo. Los embeddings viven en `tramites.embedding` (pgvector) y el grafo
en `tramites_relacionados`.

---

## 4. Cómo lo corren tus compañeros (SIN reprocesar)

Requisitos: Docker + docker compose, Python 3.11+, y una API key gratuita de
[NVIDIA Build](https://build.nvidia.com) — **solo para usar el chat en vivo**
(embeddings de la consulta + síntesis). Cargar los datos **no** necesita key.

```bash
# 1. Código
git clone git@github.com:tarnishedAlexander/superAMI.git
cd superAMI

# 2. Entorno Python
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 3. Configuración: copiar el ejemplo y poner SU propia NVIDIA_API_KEY
cp .env.example .env
#    editar .env -> NVIDIA_API_KEY=nvapi-...   (las demás vars ya vienen con default)

# 4. Base de datos (Postgres + pgvector en localhost:5433)
docker compose up -d
#    al levantar por primera vez crea el esquema (incluida la vista dominio_mvp)

# 5. Importar los datos YA procesados  ← esto reemplaza a `ingest.load`
scripts/import_db.sh ami-20260719.dump
#    imprime la verificación: 1739 trámites | 1739 con embedding | 4089 relacionados

# 6. Correr el API
.venv/bin/uvicorn api.main:app --port 8000
```

> **Importante:** NO corran `python -m ingest.load` — ese es el paso de
> reprocesamiento (embeddings) que el dump ya trae hecho. Tampoco
> `ingest.relacionados`. El dump tiene ambos.

### Probar el chat

```bash
curl -N -X POST localhost:8000/chat -H 'content-type: application/json' \
  -d '{"mensaje": "¿qué es el catastro?"}'
```

Respuesta en stream SSE (eventos `answer` con deltas y un `{"done": true,
"tramite_ids": [...]}` final; `clarification` si la consulta es ambigua).

> Nota: al 2026-07 el modelo potente por defecto (`meta/llama-3.3-70b-instruct`)
> de la capa gratuita de NIM estaba intermitente. Si la síntesis falla, poné en
> `.env` un modelo alternativo que sí responde, por ejemplo:
> `MODELO_POTENTE=nvidia/nemotron-3-super-120b-a12b`

---

## 5. Qué mandarles exactamente

Dos cosas, y con eso corren sin reprocesar:

1. **El código** — la rama `main` con el diff actual. Lo más simple:
   `git push` y que hagan `git pull` (o `git clone`). El código y el dump
   **deben coincidir**: la vista `dominio_mvp` y `listar_categorias_dominio`
   son parte del código, sin ellas el API no arranca contra estos datos.

2. **El dump** — `ami-20260719.dump` (9.4 MB). Opciones:
   - Commitearlo al repo (cabe, GitHub aguanta <100 MB) y viaja con el `git pull`.
   - O mandarlo aparte (Drive / WeTransfer) y que lo dejen en la raíz de `superAMI/`.

Lo que **NO** hace falta mandar ni que ellos regeneren: embeddings,
`tramites_relacionados`, ni nada de `ingest/`. Todo eso está en el dump.

---

## 6. Regenerar el dump (si algún día actualizan los datos)

```bash
scripts/export_db.sh                 # crea ami-AAAAMMDD.dump con la fecha de hoy
```
