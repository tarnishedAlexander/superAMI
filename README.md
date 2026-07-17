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

> En Windows el intérprete del venv es `venv\Scripts\python.exe` (y
> `venv\Scripts\pip.exe`); reemplazar `.venv/bin/...` por esa ruta en todos
> los comandos.

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

## Sync semanal

El dataset se actualiza los domingos. Correr:

    venv/Scripts/python.exe -m ingest.sync --dry-run   # ver qué cambiaría
    venv/Scripts/python.exe -m ingest.sync             # aplicar

Idempotente: solo procesa registros nuevos/modificados (diff por fechaActualización)
y marca bajas como `activo=false`. Agendable con cron / Programador de tareas de Windows.

## Calibración del gate

    venv/Scripts/python.exe tests/verificar_eval.py    # etiquetas del eval vs DB
    venv/Scripts/python.exe tests/eval_retrieval.py    # corrida informativa
    venv/Scripts/python.exe tests/calibrar_gate.py     # barrido de umbrales

Tras una sesión con usuarios, las frases quedan en `consultas_log`; incorporarlas
al eval (`tests/eval_dataset.py`) y re-correr el barrido.

## Providers open-source (opcional)

    venv/Scripts/python.exe -m pip install -r requirements-oss.txt
    # en .env: PROVIDER=ollama (ver .env.example)

NIM sigue siendo el default. Comparar calidad antes de migrar:

    venv/Scripts/python.exe tests/eval_comparativo.py --embeddings
    venv/Scripts/python.exe tests/eval_comparativo.py --sintesis 5
