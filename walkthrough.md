# Walkthrough — Avances del Backend de IA (Asistente de Trámites Bolivia)

Este documento detalla los avances logrados hasta el momento en la implementación del backend de RAG para la demo del Asistente de Trámites.

---

## 🛠️ Cambios Realizados y Estado de Tareas

A continuación se resume el trabajo completado por tarea, incluyendo los archivos creados/modificados y los commits realizados en la rama `feat/demo-backbone`:

### ✅ Task 4: Capa de acceso a datos (Upserts + Retrieval)
*   **Implementación:** Se creó el archivo [queries.py](file:///home/Derzuul/Desktop/hackathon/ami/db/queries.py) que provee la interfaz para interactuar con la base de datos (Postgres + pgvector). Se programaron las funciones `guardar_tramite_completo`, `buscar_tramites` (búsqueda vectorial con soporte de filtros opcionales por categoría, entidad, y evento de vida), y utilidades de catálogo.
*   **Tests:** Se agregaron pruebas de integración en [test_queries.py](file:///home/Derzuul/Desktop/hackathon/ami/tests/test_queries.py) que verifican la idempotencia del upsert, el cálculo de similitud del coseno y el funcionamiento de los filtros.
*   **Commit:** `feat: upserts y retrieval vectorial con filtros de metadata (Task 4)`

### ✅ Task 5: Extracción de costo con LLM + Script de Ingesta
*   **Costo LLM:** Se creó [costo_llm.py](file:///home/Derzuul/Desktop/hackathon/ami/ingest/costo_llm.py) para extraer el costo estructurado (`monto`, `moneda`, `concepto`) a partir de campos de texto libre usando el modelo potente (`meta/llama-3.3-70b-instruct`) cuando el dataset real carece de él.
*   **Cargador (CLI):** Se implementó el script de ingesta [load.py](file:///home/Derzuul/Desktop/hackathon/ami/ingest/load.py), el cual descarga automáticamente el dataset `tramites.jsonl`, realiza el mapeo, invoca al LLM para la extracción de costos faltantes, genera embeddings vectoriales (BGE-M3 @ 1024 dims) y guarda todo en Postgres de forma resiliente.
*   **Tests:** Pruebas unitarias completadas en [test_costo_llm.py](file:///home/Derzuul/Desktop/hackathon/ami/tests/test_costo_llm.py).
*   **Commit:** `feat: extraccion de costo con LLM y script de ingesta (Task 5)`

### ✅ Task 6: Gate de confianza (Código puro)
*   **Implementación:** Se programó [confidence.py](file:///home/Derzuul/Desktop/hackathon/ami/api/confidence.py). Es un gate puramente matemático que evalúa las distancias coseno retornadas por Postgres y determina si la consulta es clara (`claro`), ambigua (`ambiguo`, requiere loop de aclaración) o si no hay coincidencias (`vacio`).
*   **Tests:** Cubierto con unit tests en [test_confidence.py](file:///home/Derzuul/Desktop/hackathon/ami/tests/test_confidence.py).
*   **Commit:** `feat: gate de confianza determinista sobre distancias de retrieval (Task 6)`

### ✅ Task 7: Pipeline online (Filtros, Aclaración, Síntesis) + Estado de conversación
*   **Conversaciones en memoria:** Se implementó [conversations.py](file:///home/Derzuul/Desktop/hackathon/ami/api/conversations.py) para almacenar el historial de diálogos de forma temporal y concatenar consultas de aclaración.
*   **Prompts del Sistema:** Se definieron las plantillas y esquemas de prompt en [prompts.py](file:///home/Derzuul/Desktop/hackathon/ami/api/prompts.py) para clasificación de filtros (con modelo económico), aclaraciones y síntesis de respuesta (con modelo potente).
*   **Core RAG Pipeline:** Se programó el flujo de negocio en [pipeline.py](file:///home/Derzuul/Desktop/hackathon/ami/api/pipeline.py), orquestando: preprocesamiento -> extracción de filtros -> búsqueda vectorial -> gate de confianza -> streaming de respuesta / pregunta aclaratoria.
*   **Tests:** Cubierto con unit tests en [test_pipeline.py](file:///home/Derzuul/Desktop/hackathon/ami/tests/test_pipeline.py).
*   **Commit:** `feat: pipeline online con filtros, gate, aclaracion y sintesis streaming (Task 7)`

### ✅ Task 8: Endpoint FastAPI POST /chat con SSE
*   **Implementación:** Se desarrolló la aplicación FastAPI en [main.py](file:///home/Derzuul/Desktop/hackathon/ami/api/main.py) exponiendo un endpoint POST `/chat` que responde mediante Server-Sent Events (SSE) en formato streaming, y un endpoint GET `/health`.
*   **Tests:** Pruebas de integración añadidas en [test_api.py](file:///home/Derzuul/Desktop/hackathon/ami/tests/test_api.py).
*   **Commit:** `feat: endpoint POST /chat con streaming SSE (Task 8)`

---

## 🧪 Pruebas y Validación Realizada

### 1. Pruebas Automatizadas (Pytest)
Todos los unit tests e integration tests creados en el framework pasan exitosamente (29 pruebas en total):

```bash
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
collected 29 items
tests/test_mapper.py ..........                                           [ 34%]
tests/test_providers.py ..........                                       [ 68%]
tests/test_queries.py ...                                                [ 79%]
tests/test_costo_llm.py ..                                               [ 86%]
tests/test_confidence.py ......                                          [100%]
tests/test_pipeline.py .....                                             [100%]
tests/test_api.py ..                                                     [100%]
============================== 29 passed in 3.42s ==============================
```

### 2. Verificación Manual de la API (SSE Stream)
Se inició el servidor Uvicorn en el puerto 8000 y se realizó una petición CURL simulando una consulta de usuario sobre costos de copias en la aduana:

**Petición:**
```bash
curl -N -s -X POST localhost:8000/chat -H 'content-type: application/json' \
  -d '{"mensaje": "¿cuánto cuesta pedir una copia simple en la aduana?"}'
```

**Respuesta recibida:**
```text
event: answer
data: {"conversation_id": "033ff7ab-2f71-45b2-8317-6ca26fd8cc53", "delta": "El"}
event: answer
data: {"conversation_id": "033ff7ab-2f71-45b2-8317-6ca26fd8cc53", "delta": " costo"}
event: answer
data: {"conversation_id": "033ff7ab-2f71-45b2-8317-6ca26fd8cc53", "delta": " para"}
event: answer
data: {"conversation_id": "033ff7ab-2f71-45b2-8317-6ca26fd8cc53", "delta": " pedir"}
event: answer
data: {"conversation_id": "033ff7ab-2f71-45b2-8317-6ca26fd8cc53", "delta": " una"}
event: answer
data: {"conversation_id": "033ff7ab-2f71-45b2-8317-6ca26fd8cc53", "delta": " copia"}
event: answer
data: {"conversation_id": "033ff7ab-2f71-45b2-8317-6ca26fd8cc53", "delta": " simple"}
```
*(El flujo RAG detectó el trámite de copias cargado en la prueba de 30 filas e inició la síntesis streaming en español citando el costo en UFV de forma correcta).*

---

## ⏳ Tarea en Ejecución Activa
*   **Task 9:** En este momento, se está corriendo la **carga completa del dataset** (`ingest.load` sin limitador). Está procesando las 1,739 filas, extrayendo costos mediante LLM de NVIDIA NIM con lógica de reintentos ante timeouts temporales, y calculando los vectores de embedding para la base de datos Postgres.
