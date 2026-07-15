# MVP Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar la Fase 2 del ROADMAP (solo backend): gate recalibrado con veredicto "lejano", tope de aclaración, logging de consultas, eval ampliado, sync incremental, fetch en vivo, conversación persistente, trámites relacionados y providers open-source opcionales.

**Architecture:** Se extiende el pipeline existente (FastAPI + Postgres/pgvector + providers NIM) sin reestructurar: cambios de esquema idempotentes en `db/schema.sql`, funciones nuevas en `db/queries.py`, módulos nuevos `ingest/sync.py`, `api/live_fetch.py`, `ingest/relacionados.py`, `providers/st_embeddings.py`. Spec: `docs/superpowers/specs/2026-07-14-mvp-backend-design.md`.

**Tech Stack:** Python, FastAPI, psycopg 3, pgvector, NVIDIA NIM (OpenAI-compat), httpx, BeautifulSoup4 (nuevo), sentence-transformers + numpy (opcionales).

## Global Constraints

- Intérprete en esta máquina Windows: `venv/Scripts/python.exe` (NO `.venv/bin`). Tests: `venv/Scripts/python.exe -m pytest tests/ -q`.
- Los tests de integración requieren Docker Desktop corriendo y `docker compose up -d` (Postgres en localhost:5433).
- Cambios de esquema: statements idempotentes agregados a `db/schema.sql` y aplicados con `docker compose exec -T db psql -U ami -d ami < db/schema.sql` (el volumen initdb solo corre en la primera inicialización).
- TDD estricto; la suite completa queda verde al cierre de cada task. Código, strings de usuario y commits en español (convención del repo).
- Proveedor default: `PROVIDER=nvidia`. Mantener patrones fail-open (filtros) / fail-soft (logging, fetch): un error en pasos auxiliares nunca rompe la respuesta.
- Dependencia base nueva permitida: `beautifulsoup4`. Opcionales SOLO en `requirements-oss.txt`: `sentence-transformers`, `numpy`.
- Valores del spec (copiados verbatim): tope de **1** ronda de aclaración; TTL de `fetch_cache` **7 días**; limpieza de conversaciones **>24h**; candidatos relacionados **top-5 por trámite**; validación previa con muestra de **~20 trámites**; embeddings locales `intfloat/multilingual-e5-base` (**768 dims**, eval offline sin tocar la DB). Criterios de éxito: hit@5 ≥ 90% en satisfacibles; **cero** "claro" incorrecto; ≤ 25% de directas caen en aclaración innecesaria; 100% de no satisfacibles sin inventar.
- Decisión de plan (deriva del "supuesto a verificar" del spec): el diff del sync se implementa comparando `fechaActualización` del jsonl contra `last_updated` de la DB — cubre altas, modificaciones y bajas sin depender del formato de los CSVs de terceros. La Task 9 inspecciona los CSVs reales y registra la decisión en `DECISIONS.md`.

---

## Etapa 1 — Experiencia de respuesta

### Task 1: Veredicto "lejano" en el gate de confianza

**Files:**
- Modify: `api/confidence.py`
- Test: `tests/test_confidence.py`

**Interfaces:**
- Produces: `evaluar_confianza(distancias, umbral_gap=UMBRAL_GAP, umbral_distancia_max=UMBRAL_DISTANCIA_MAX) -> Literal["claro", "ambiguo", "vacio", "lejano"]`. "lejano" = d1 > umbral_distancia_max (antes devolvía "ambiguo" en ese caso). Tasks 3 y 11 consumen "lejano".

- [ ] **Step 1: Escribir los tests que fallan**

Reemplazar el contenido de `tests/test_confidence.py` (los tests existentes pasan umbrales explícitos donde dependan de valores, para que la recalibración de la Task 7 no los rompa):

```python
from api.confidence import evaluar_confianza


def test_vacio():
    assert evaluar_confianza([]) == "vacio"


def test_claro_con_gap_grande():
    assert evaluar_confianza([0.20, 0.40, 0.45], umbral_gap=0.03, umbral_distancia_max=0.52) == "claro"


def test_ambiguo_con_gap_chico():
    assert evaluar_confianza([0.30, 0.32, 0.45], umbral_gap=0.03, umbral_distancia_max=0.52) == "ambiguo"


def test_lejano_si_top1_supera_distancia_max():
    # antes esto era "ambiguo"; ahora es señal de que el trámite no está en la DB
    assert evaluar_confianza([0.80, 0.99], umbral_gap=0.03, umbral_distancia_max=0.52) == "lejano"


def test_claro_resultado_unico_cercano():
    assert evaluar_confianza([0.20], umbral_gap=0.03, umbral_distancia_max=0.52) == "claro"


def test_lejano_resultado_unico_lejos():
    assert evaluar_confianza([0.70], umbral_gap=0.03, umbral_distancia_max=0.52) == "lejano"


def test_umbral_es_parametrizable():
    assert evaluar_confianza([0.30, 0.32], umbral_gap=0.01, umbral_distancia_max=0.52) == "claro"
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `venv/Scripts/python.exe -m pytest tests/test_confidence.py -v`
Expected: FAIL en `test_lejano_si_top1_supera_distancia_max` y `test_lejano_resultado_unico_lejos` (devuelven "ambiguo").

- [ ] **Step 3: Implementar el veredicto nuevo**

En `api/confidence.py`, cambiar la firma y la primera rama (el comentario de calibración existente se conserva; la Task 7 lo reescribe):

```python
def evaluar_confianza(
    distancias: list[float],
    umbral_gap: float = UMBRAL_GAP,
    umbral_distancia_max: float = UMBRAL_DISTANCIA_MAX,
) -> Literal["claro", "ambiguo", "vacio", "lejano"]:
    """Gate determinista sobre las distancias coseno del retrieval (ascendentes).

    - "lejano": el mejor match está más allá de umbral_distancia_max — el trámite
      probablemente no está en la DB; preguntar no lo va a hacer aparecer.
    - "ambiguo": gap chico entre top-1 y top-2 con d1 razonable — ambigüedad
      genuina entre candidatos, corresponde aclarar.
    """
    if not distancias:
        return "vacio"
    if distancias[0] > umbral_distancia_max:
        return "lejano"
    if len(distancias) == 1:
        return "claro"
    if distancias[1] - distancias[0] >= umbral_gap:
        return "claro"
    return "ambiguo"
```

- [ ] **Step 4: Correr la suite y verificar que pasa**

Run: `venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS. Nota: `api/pipeline.py` todavía trata cualquier veredicto no-"ambiguo" como claro; los hits lejanos ahora caen en la rama de síntesis hasta la Task 3 — los tests de pipeline existentes usan distancias cercanas, no se ven afectados.

- [ ] **Step 5: Commit**

```bash
git add api/confidence.py tests/test_confidence.py
git commit -m "feat: veredicto lejano en el gate de confianza (Task 1 MVP)"
```

---

### Task 2: Turnos tipados en ConversationStore

**Files:**
- Modify: `api/conversations.py`
- Test: `tests/test_conversations.py` (nuevo)

**Interfaces:**
- Produces: `ConversationStore.append(conversation_id, role, content, tipo=None)` (tipo default = role); `ConversationStore.contar_aclaraciones(conversation_id) -> int`; `mensajes()` sigue devolviendo SOLO `{"role", "content"}` (compatible con providers). Task 3 consume `contar_aclaraciones`; Task 12 replica esta interfaz en Postgres.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `tests/test_conversations.py`:

```python
from api.conversations import ConversationStore


def test_append_con_tipo_y_conteo_de_aclaraciones():
    store = ConversationStore()
    cid = store.get_or_create(None)
    store.append(cid, "user", "papel del carro")
    store.append(cid, "assistant", "¿Te referís a A o B?", tipo="clarification")
    store.append(cid, "user", "el de propiedad")
    assert store.contar_aclaraciones(cid) == 1


def test_mensajes_no_expone_tipo():
    store = ConversationStore()
    cid = store.get_or_create(None)
    store.append(cid, "assistant", "hola", tipo="answer")
    assert store.mensajes(cid) == [{"role": "assistant", "content": "hola"}]


def test_tipo_default_es_el_role():
    store = ConversationStore()
    cid = store.get_or_create(None)
    store.append(cid, "user", "hola")
    assert store.contar_aclaraciones(cid) == 0
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `venv/Scripts/python.exe -m pytest tests/test_conversations.py -v`
Expected: FAIL con `TypeError: append() got an unexpected keyword argument 'tipo'` (o AttributeError por `contar_aclaraciones`).

- [ ] **Step 3: Implementar**

Reemplazar `api/conversations.py`:

```python
import uuid


class ConversationStore:
    """Historial en memoria por conversation_id. Se pierde al reiniciar (queda para tests;
    el server usa PostgresConversationStore desde la Etapa 4)."""

    def __init__(self):
        self._historiales: dict[str, list[dict]] = {}

    def get_or_create(self, conversation_id: str | None) -> str:
        cid = conversation_id or str(uuid.uuid4())
        self._historiales.setdefault(cid, [])
        return cid

    def append(self, conversation_id: str, role: str, content: str, tipo: str | None = None) -> None:
        self._historiales[conversation_id].append(
            {"role": role, "content": content, "tipo": tipo or role}
        )

    def mensajes(self, conversation_id: str) -> list[dict]:
        return [
            {"role": m["role"], "content": m["content"]}
            for m in self._historiales.get(conversation_id, [])
        ]

    def texto_de_consulta(self, conversation_id: str) -> str:
        return " ".join(
            m["content"] for m in self._historiales.get(conversation_id, []) if m["role"] == "user"
        )

    def contar_aclaraciones(self, conversation_id: str) -> int:
        return sum(
            1 for m in self._historiales.get(conversation_id, []) if m.get("tipo") == "clarification"
        )
```

- [ ] **Step 4: Correr la suite completa**

Run: `venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS (append sin `tipo` sigue funcionando por el default).

- [ ] **Step 5: Commit**

```bash
git add api/conversations.py tests/test_conversations.py
git commit -m "feat: turnos tipados y conteo de aclaraciones en ConversationStore (Task 2 MVP)"
```

---

### Task 3: Tope de 1 aclaración + ruta "lejano" en el pipeline

**Files:**
- Modify: `api/pipeline.py`
- Modify: `api/prompts.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `evaluar_confianza` con veredicto "lejano" (Task 1); `store.contar_aclaraciones` y `append(..., tipo=)` (Task 2).
- Produces: `system_de_sintesis(tramite, alternativas=None)` en prompts (Task 15 le agrega `relacionados=`); rama única `veredicto in ("vacio", "lejano")` que llama `fetch_live_fallback` (la Task 11 la reemplaza por el fetch real).

- [ ] **Step 1: Escribir los tests que fallan**

Primero, en los tests ambiguos EXISTENTES (`test_caso_ambiguo_pregunta_aclaracion` y `test_aclaracion_concatena_consulta`) cambiar las distancias `0.30, 0.31` por `0.300, 0.301` (gap 0.001): la Task 7 recalibra los umbrales default y el barrido nunca baja de gap 0.005, así los casos ambiguos de los tests siguen siéndolo con cualquier calibración.

Luego agregar al final de `tests/test_pipeline.py`:

```python
def test_lejano_responde_no_encontrado(monkeypatch):
    # d1 > 0.52 (umbral default): el gate dice "lejano", no se pregunta ni se sintetiza
    _preparar(monkeypatch, [_hit(1, "ALGO LEJANO", 0.80), _hit(2, "OTRO", 0.99)])
    deps = _deps()
    cid = deps.store.get_or_create(None)
    eventos = list(procesar_mensaje(deps, cid, "algo rarísimo"))
    assert eventos[0][0] == "answer"
    assert "No encontré" in eventos[0][1]["delta"]


def test_segunda_ambigua_fuerza_respuesta(monkeypatch):
    # primera pasada ambigua -> aclaración; segunda pasada ambigua -> respuesta forzada
    _preparar(monkeypatch, [_hit(1, "A", 0.300), _hit(2, "B", 0.301)])
    deps = _deps()
    cid = deps.store.get_or_create(None)
    eventos1 = list(procesar_mensaje(deps, cid, "papel"))
    assert eventos1[0][0] == "clarification"
    eventos2 = list(procesar_mensaje(deps, cid, "no sé, el común"))
    assert eventos2[0][0] == "answer"
    assert eventos2[-1] == ("answer", {"done": True, "tramite_ids": [1]})


def test_respuesta_forzada_pasa_alternativas_al_prompt(monkeypatch):
    _preparar(monkeypatch, [_hit(1, "A", 0.300), _hit(2, "B", 0.301), _hit(3, "C", 0.330)])
    capturado = {}

    class ChatEspia(FakeChat):
        def stream(self, *, system, messages, max_tokens=4096):
            capturado["system"] = system
            yield "ok"

    deps = _deps(potente=ChatEspia())
    cid = deps.store.get_or_create(None)
    list(procesar_mensaje(deps, cid, "papel"))          # aclaración
    list(procesar_mensaje(deps, cid, "sigo sin saber"))  # forzada
    assert "B" in capturado["system"] and "C" in capturado["system"]
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `venv/Scripts/python.exe -m pytest tests/test_pipeline.py -v`
Expected: FAIL — `test_lejano_responde_no_encontrado` sintetiza en vez de no-encontrado; `test_segunda_ambigua_fuerza_respuesta` devuelve una segunda clarification.

- [ ] **Step 3: Extender el prompt de síntesis con alternativas**

En `api/prompts.py`, reemplazar `system_de_sintesis`:

```python
def system_de_sintesis(tramite: dict, alternativas: list[dict] | None = None) -> str:
    datos = {k: v for k, v in tramite.items() if k != "distancia"}
    base = SISTEMA_SINTESIS + "\n\n<tramite>\n" + json.dumps(datos, ensure_ascii=False, default=str) + "\n</tramite>"
    if alternativas:
        lineas = "\n".join(
            f"- {a['nombre']} ({a.get('entidad_nombre') or 'entidad desconocida'})" for a in alternativas
        )
        base += (
            "\n\nAtención: la coincidencia con la consulta NO es segura. Abrí la respuesta aclarando qué "
            'trámite estás mostrando (ej. "Te muestro el que mejor coincide con tu consulta: ...") y cerrá '
            "mencionando en una línea estas alternativas por si buscaba otra cosa:\n" + lineas
        )
    return base
```

- [ ] **Step 4: Reescribir el flujo de `procesar_mensaje`**

En `api/pipeline.py`, reemplazar `procesar_mensaje` completo (los cambios: rama única vacio/lejano vía veredicto, tope de aclaración, `tipo=` en los appends, alternativas en la síntesis forzada):

```python
def procesar_mensaje(deps: Deps, conversation_id: str, mensaje: str) -> Iterator[tuple[str, dict]]:
    try:
        deps.store.append(conversation_id, "user", mensaje)
        consulta = deps.store.texto_de_consulta(conversation_id)

        filtros = inferir_filtros(deps, consulta)
        embedding = deps.embedder.embed_query(consulta)

        with get_connection() as conn:
            hits = buscar_tramites(conn, embedding, **filtros)
            if not hits and filtros:
                # fail-open: los filtros pueden haber sido mal inferidos
                hits = buscar_tramites(conn, embedding)

        veredicto = evaluar_confianza([h["distancia"] for h in hits])

        if veredicto in ("vacio", "lejano"):
            fetch_live_fallback(consulta)
            deps.store.append(conversation_id, "assistant", MENSAJE_NO_ENCONTRADO, tipo="not_found")
            yield ("answer", {"delta": MENSAJE_NO_ENCONTRADO})
            yield ("answer", {"done": True, "tramite_ids": []})
            return

        if veredicto == "ambiguo" and deps.store.contar_aclaraciones(conversation_id) == 0:
            pregunta = formular_aclaracion(deps, consulta, hits[:3])
            deps.store.append(conversation_id, "assistant", pregunta, tipo="clarification")
            yield ("clarification", {"text": pregunta})
            return

        # claro, o ambiguo con el tope de 1 aclaración alcanzado: responder igual, con transparencia
        forzado = veredicto == "ambiguo"
        top = hits[0]
        alternativas = (
            [{"nombre": h["nombre"], "entidad_nombre": h.get("entidad_nombre")} for h in hits[1:4]]
            if forzado
            else None
        )
        partes: list[str] = []
        for delta in deps.chat_potente.stream(
            system=system_de_sintesis(top, alternativas=alternativas),
            messages=deps.store.mensajes(conversation_id),
            max_tokens=4096,
        ):
            partes.append(delta)
            yield ("answer", {"delta": delta})
        deps.store.append(conversation_id, "assistant", "".join(partes), tipo="answer")
        yield ("answer", {"done": True, "tramite_ids": [top["id"]]})
    except Exception:
        logger.exception("error procesando mensaje en conversación %s", conversation_id)
        yield ("error", {"message": MENSAJE_ERROR})
```

- [ ] **Step 5: Correr la suite completa**

Run: `venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS, incluidos los 3 tests nuevos y los existentes (el caso ambiguo de primera ronda sigue preguntando).

- [ ] **Step 6: Commit**

```bash
git add api/pipeline.py api/prompts.py tests/test_pipeline.py
git commit -m "feat: tope de 1 aclaracion y ruta lejano en el pipeline (Task 3 MVP)"
```

---

### Task 4: Prompt de síntesis para datos ausentes en la ficha

**Files:**
- Modify: `api/prompts.py`
- Test: `tests/test_prompts.py` (nuevo)

**Interfaces:**
- Produces: `SISTEMA_SINTESIS` con la regla nueva; sin cambios de firma.

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_prompts.py`:

```python
from api.prompts import system_de_sintesis


def _tramite():
    return {"id": 1, "nombre": "TRAMITE X", "enlaces": [], "distancia": 0.2}


def test_sintesis_instruye_que_hacer_con_datos_ausentes():
    system = system_de_sintesis(_tramite())
    assert "SÍ tiene la ficha" in system


def test_sintesis_incluye_el_tramite():
    assert '"nombre": "TRAMITE X"' in system_de_sintesis(_tramite())
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `venv/Scripts/python.exe -m pytest tests/test_prompts.py -v`
Expected: FAIL en `test_sintesis_instruye_que_hacer_con_datos_ausentes`.

- [ ] **Step 3: Ajustar la regla en `SISTEMA_SINTESIS`**

En `api/prompts.py`, reemplazar la línea:

```
- Si un dato que el ciudadano pide no está en <tramite>, decilo explícitamente ("ese dato no figura en la ficha del trámite").
```

por:

```
- Si un dato que el ciudadano pide no está en <tramite>: decilo sin rodeos, resumí en una o dos líneas qué información SÍ tiene la ficha (requisitos, costo, dónde se hace), y si hay una URL en enlaces o modalidades indicá que ahí puede figurar el dato faltante. Nunca respondas solo "ese dato no figura".
```

- [ ] **Step 4: Correr la suite completa**

Run: `venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/prompts.py tests/test_prompts.py
git commit -m "feat: sintesis util cuando el dato pedido no figura en la ficha (Task 4 MVP)"
```

---

### Task 5: Logging de consultas (`consultas_log`)

**Files:**
- Modify: `db/schema.sql`
- Modify: `db/queries.py`
- Modify: `api/pipeline.py`
- Test: `tests/test_pipeline.py`, `tests/test_queries.py`

**Interfaces:**
- Produces: `registrar_consulta(conn, datos: dict) -> None` en `db/queries.py` (claves: conversation_id, mensaje, consulta_acumulada, filtros, top_ids, top_distancias, veredicto, respuesta_tipo); helper privado `_registrar_consulta(...)` fail-soft en pipeline. La Task 7 y la sesión con usuarios consumen esta tabla.

- [ ] **Step 1: Agregar la tabla al esquema y aplicarla**

Agregar al final de `db/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS consultas_log (
  id bigserial PRIMARY KEY,
  ts timestamptz NOT NULL DEFAULT now(),
  conversation_id text,
  mensaje text NOT NULL,
  consulta_acumulada text,
  filtros jsonb,
  top_ids integer[],
  top_distancias real[],
  veredicto text,
  respuesta_tipo text
);
```

Run: `docker compose exec -T db psql -U ami -d ami < db/schema.sql`
Expected: `CREATE TABLE` (y NOTICEs "already exists" del resto, inofensivos).

- [ ] **Step 2: Test de integración de la query (falla)**

Agregar al final de `tests/test_queries.py`:

```python
def test_registrar_consulta(conn):
    from db.queries import registrar_consulta

    registrar_consulta(conn, {
        "conversation_id": "test-log-1", "mensaje": "hola", "consulta_acumulada": "hola",
        "filtros": {"categoria_slug": "empresas"}, "top_ids": [1, 2],
        "top_distancias": [0.2, 0.4], "veredicto": "claro", "respuesta_tipo": "answer",
    })
    conn.commit()
    fila = conn.execute(
        "SELECT veredicto, respuesta_tipo, top_ids FROM consultas_log WHERE conversation_id = 'test-log-1'"
    ).fetchone()
    assert fila == ("claro", "answer", [1, 2])
    conn.execute("DELETE FROM consultas_log WHERE conversation_id = 'test-log-1'")
    conn.commit()
```

Run: `venv/Scripts/python.exe -m pytest tests/test_queries.py::test_registrar_consulta -v`
Expected: FAIL con ImportError (`registrar_consulta` no existe).

- [ ] **Step 3: Implementar la query**

Agregar al final de `db/queries.py`:

```python
def registrar_consulta(conn, datos: dict) -> None:
    conn.execute(
        """
        INSERT INTO consultas_log
          (conversation_id, mensaje, consulta_acumulada, filtros, top_ids, top_distancias, veredicto, respuesta_tipo)
        VALUES (%(conversation_id)s, %(mensaje)s, %(consulta_acumulada)s, %(filtros)s,
                %(top_ids)s, %(top_distancias)s, %(veredicto)s, %(respuesta_tipo)s)
        """,
        {**datos, "filtros": Json(datos["filtros"]) if datos.get("filtros") else None},
    )
```

Run: `venv/Scripts/python.exe -m pytest tests/test_queries.py -q` → Expected: PASS.

- [ ] **Step 4: Tests del pipeline (fallan)**

En `tests/test_pipeline.py`, reemplazar `_conn_fake` por una versión con `commit` (el fake actual yield-ea `None` y el helper de log lo llama):

```python
class _FakeConn:
    def commit(self):
        pass


@contextmanager
def _conn_fake():
    yield _FakeConn()
```

y agregar al final:

```python
def test_registra_consulta_en_caso_claro(monkeypatch):
    _preparar(monkeypatch, [_hit(1, "CEDULA", 0.2), _hit(2, "PASAPORTE", 0.4)])
    registros = []
    monkeypatch.setattr(pipeline, "registrar_consulta", lambda conn, datos: registros.append(datos))
    deps = _deps()
    cid = deps.store.get_or_create(None)
    list(procesar_mensaje(deps, cid, "quiero sacar mi carnet"))
    assert len(registros) == 1
    assert registros[0]["veredicto"] == "claro"
    assert registros[0]["respuesta_tipo"] == "answer"
    assert registros[0]["top_ids"] == [1, 2]


def test_log_roto_no_rompe_la_respuesta(monkeypatch):
    _preparar(monkeypatch, [_hit(1, "CEDULA", 0.2), _hit(2, "PASAPORTE", 0.4)])

    def explota(conn, datos):
        raise RuntimeError("log caído")

    monkeypatch.setattr(pipeline, "registrar_consulta", explota)
    deps = _deps()
    cid = deps.store.get_or_create(None)
    eventos = list(procesar_mensaje(deps, cid, "quiero sacar mi carnet"))
    assert eventos[-1] == ("answer", {"done": True, "tramite_ids": [1]})
```

Run: `venv/Scripts/python.exe -m pytest tests/test_pipeline.py -v`
Expected: FAIL con AttributeError (`pipeline.registrar_consulta` no existe).

- [ ] **Step 5: Integrar el log fail-soft en el pipeline**

En `api/pipeline.py`: agregar el import y el helper, y llamarlo en cada rama. Import:

```python
from db.queries import buscar_entidad_slug, buscar_tramites, registrar_consulta
```

Helper (arriba de `procesar_mensaje`):

```python
def _registrar(conversation_id: str, mensaje: str, consulta: str, filtros: dict,
               hits: list[dict], veredicto: str | None, respuesta_tipo: str) -> None:
    """Fail-soft: el log nunca rompe la respuesta."""
    try:
        with get_connection() as conn:
            registrar_consulta(conn, {
                "conversation_id": conversation_id,
                "mensaje": mensaje,
                "consulta_acumulada": consulta,
                "filtros": filtros or None,
                "top_ids": [h["id"] for h in hits],
                "top_distancias": [round(h["distancia"], 4) for h in hits],
                "veredicto": veredicto,
                "respuesta_tipo": respuesta_tipo,
            })
            conn.commit()
    except Exception:
        logger.warning("no se pudo registrar la consulta en consultas_log", exc_info=True)
```

En `procesar_mensaje`, inicializar antes del try interior de trabajo: (al inicio del `try`) `filtros: dict = {}` y `hits: list[dict] = []` y `veredicto = None` ANTES de calcularlos (para que la rama de error pueda loguear), y agregar las llamadas:

- rama vacio/lejano, antes del primer `yield`: `_registrar(conversation_id, mensaje, consulta, filtros, hits, veredicto, "not_found")`
- rama aclaración, antes del `yield`: `_registrar(conversation_id, mensaje, consulta, filtros, hits, veredicto, "clarification")`
- rama respuesta, después del `yield` final `done`: `_registrar(conversation_id, mensaje, consulta, filtros, hits, veredicto, "answer")`
- rama `except`: `_registrar(conversation_id, mensaje, locals().get("consulta") or mensaje, locals().get("filtros") or {}, locals().get("hits") or [], locals().get("veredicto"), "error")` — o más simple: inicializar `consulta = mensaje` junto con las otras variables al inicio del try y usar las variables directamente en el except.

Versión final de `procesar_mensaje` con el log integrado:

```python
def procesar_mensaje(deps: Deps, conversation_id: str, mensaje: str) -> Iterator[tuple[str, dict]]:
    consulta = mensaje
    filtros: dict = {}
    hits: list[dict] = []
    veredicto: str | None = None
    try:
        deps.store.append(conversation_id, "user", mensaje)
        consulta = deps.store.texto_de_consulta(conversation_id)

        filtros = inferir_filtros(deps, consulta)
        embedding = deps.embedder.embed_query(consulta)

        with get_connection() as conn:
            hits = buscar_tramites(conn, embedding, **filtros)
            if not hits and filtros:
                # fail-open: los filtros pueden haber sido mal inferidos
                hits = buscar_tramites(conn, embedding)

        veredicto = evaluar_confianza([h["distancia"] for h in hits])

        if veredicto in ("vacio", "lejano"):
            fetch_live_fallback(consulta)
            deps.store.append(conversation_id, "assistant", MENSAJE_NO_ENCONTRADO, tipo="not_found")
            _registrar(conversation_id, mensaje, consulta, filtros, hits, veredicto, "not_found")
            yield ("answer", {"delta": MENSAJE_NO_ENCONTRADO})
            yield ("answer", {"done": True, "tramite_ids": []})
            return

        if veredicto == "ambiguo" and deps.store.contar_aclaraciones(conversation_id) == 0:
            pregunta = formular_aclaracion(deps, consulta, hits[:3])
            deps.store.append(conversation_id, "assistant", pregunta, tipo="clarification")
            _registrar(conversation_id, mensaje, consulta, filtros, hits, veredicto, "clarification")
            yield ("clarification", {"text": pregunta})
            return

        # claro, o ambiguo con el tope de 1 aclaración alcanzado: responder igual, con transparencia
        forzado = veredicto == "ambiguo"
        top = hits[0]
        alternativas = (
            [{"nombre": h["nombre"], "entidad_nombre": h.get("entidad_nombre")} for h in hits[1:4]]
            if forzado
            else None
        )
        partes: list[str] = []
        for delta in deps.chat_potente.stream(
            system=system_de_sintesis(top, alternativas=alternativas),
            messages=deps.store.mensajes(conversation_id),
            max_tokens=4096,
        ):
            partes.append(delta)
            yield ("answer", {"delta": delta})
        deps.store.append(conversation_id, "assistant", "".join(partes), tipo="answer")
        _registrar(conversation_id, mensaje, consulta, filtros, hits, veredicto, "answer")
        yield ("answer", {"done": True, "tramite_ids": [top["id"]]})
    except Exception:
        logger.exception("error procesando mensaje en conversación %s", conversation_id)
        _registrar(conversation_id, mensaje, consulta, filtros, hits, veredicto, "error")
        yield ("error", {"message": MENSAJE_ERROR})
```

- [ ] **Step 6: Correr la suite completa**

Run: `venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add db/schema.sql db/queries.py api/pipeline.py tests/test_pipeline.py tests/test_queries.py
git commit -m "feat: log de consultas fail-soft en consultas_log (Task 5 MVP)"
```

---

### Task 6: Eval ampliado a 60-100 frases etiquetadas

**Files:**
- Create: `tests/eval_dataset.py`
- Create: `tests/generar_variantes.py`
- Create: `tests/verificar_eval.py`
- Modify: `tests/eval_retrieval.py`

**Interfaces:**
- Produces: `tests/eval_dataset.py` con `CASOS: list[dict]` (claves: `frase`, `clase` ∈ {"directa","ambigua","no_satisfacible"}, `esperado` — substring solo para directas) y `normalizar(texto) -> str`. Task 7 y Task 17 consumen `CASOS` y `normalizar`.

- [ ] **Step 1: Crear el dataset semilla**

Crear `tests/eval_dataset.py`. Las directas marcadas `# VERIFICAR` usan trámites plausibles no confirmados — el Step 3 las valida contra la DB y se reclasifican o eliminan las que no existan:

```python
"""Dataset del eval de retrieval/gate. Tres clases:
- directa: debe responderse one-shot; `esperado` = substring del nombre del trámite correcto.
- ambigua: legítimamente ambigua; el gate debe pedir aclaración.
- no_satisfacible: no existe en el dataset gob.bo; el gate debe gatearla (lejano), no inventar.
Curado a mano; las variantes generadas (tests/generar_variantes.py) se agregan acá tras revisión.
"""
import unicodedata


def normalizar(texto: str) -> str:
    sin_acentos = unicodedata.normalize("NFD", texto)
    return "".join(c for c in sin_acentos if unicodedata.category(c) != "Mn").upper()


CASOS = [
    # --- directas (verificadas en el eval del demo, 2026-07-14) ---
    {"frase": "necesito el papel del carro", "clase": "directa", "esperado": "VEHICULO"},
    {"frase": "quiero los papeles de propiedad de mi auto", "clase": "directa", "esperado": "VEHICULO"},
    {"frase": "certificado de nacimiento", "clase": "directa", "esperado": "NACIMIENTO"},
    {"frase": "necesito el certificado de nacimiento de mi hija", "clase": "directa", "esperado": "NACIMIENTO"},
    {"frase": "registrar a mi hijo recién nacido", "clase": "directa", "esperado": "NACIMIENTO"},
    {"frase": "sacar el NIT para mi negocio", "clase": "directa", "esperado": "NIT"},
    {"frase": "cómo me inscribo en impuestos para poder facturar", "clase": "directa", "esperado": "NIT"},
    {"frase": "certificado de antecedentes penales", "clase": "directa", "esperado": "ANTECEDENTES"},
    {"frase": "necesito mi certificado de antecedentes para un trabajo", "clase": "directa", "esperado": "ANTECEDENTES"},
    {"frase": "quiero abrir mi empresa", "clase": "directa", "esperado": "EMPRESA"},
    {"frase": "registrar mi empresa para que sea legal", "clase": "directa", "esperado": "EMPRESA"},
    {"frase": "cobrar la renta dignidad", "clase": "directa", "esperado": "RENTA DIGNIDAD"},
    {"frase": "mi abuelita quiere cobrar su bono de vejez", "clase": "directa", "esperado": "RENTA DIGNIDAD"},
    {"frase": "quiero poner una farmacia", "clase": "directa", "esperado": "FARMACIA"},
    {"frase": "qué necesito para abrir una farmacia en mi barrio", "clase": "directa", "esperado": "FARMACIA"},
    {"frase": "carnet de discapacidad", "clase": "directa", "esperado": "DISCAPACIDAD"},
    {"frase": "cómo saco el carnet de discapacidad para mi hermano", "clase": "directa", "esperado": "DISCAPACIDAD"},
    {"frase": "título de bachiller", "clase": "directa", "esperado": "BACHILLER"},
    {"frase": "perdí mi título de bachiller, necesito otro", "clase": "directa", "esperado": "BACHILLER"},
    {"frase": "apostillar mis documentos para salir del país", "clase": "directa", "esperado": "APOSTILLA"},
    {"frase": "legalizar documentos para usarlos en el extranjero", "clase": "directa", "esperado": "APOSTILLA"},
    {"frase": "certificado de defunción", "clase": "directa", "esperado": "DEFUNCION"},
    {"frase": "falleció mi papá y necesito el certificado", "clase": "directa", "esperado": "DEFUNCION"},
    # --- directas plausibles (VERIFICAR contra la DB en el Step 3) ---
    {"frase": "registrar la marca de mi producto", "clase": "directa", "esperado": "MARCA"},  # VERIFICAR
    {"frase": "registro sanitario para vender alimentos", "clase": "directa", "esperado": "SANITARIO"},  # VERIFICAR
    {"frase": "quiero exportar mis productos, qué necesito", "clase": "directa", "esperado": "EXPORTA"},  # VERIFICAR
    {"frase": "personería jurídica para nuestra asociación", "clase": "directa", "esperado": "PERSONALIDAD JURIDICA"},  # VERIFICAR
    {"frase": "duplicado de la libreta de servicio militar", "clase": "directa", "esperado": "LIBRETA"},  # VERIFICAR
    {"frase": "inscribirme al SUS para atenderme en el hospital", "clase": "directa", "esperado": "SUS"},  # VERIFICAR
    {"frase": "quiero tramitar mi jubilación", "clase": "directa", "esperado": "JUBILACION"},  # VERIFICAR
    # --- ambiguas legítimas (el gate debe preguntar) ---
    {"frase": "necesito un certificado", "clase": "ambigua", "esperado": None},
    {"frase": "quiero sacar un documento", "clase": "ambigua", "esperado": None},
    {"frase": "trámites para mi negocio", "clase": "ambigua", "esperado": None},
    {"frase": "necesito registrar una propiedad", "clase": "ambigua", "esperado": None},
    {"frase": "papeles para viajar", "clase": "ambigua", "esperado": None},
    {"frase": "necesito un certificado para el banco", "clase": "ambigua", "esperado": None},
    {"frase": "quiero registrar a mi familia", "clase": "ambigua", "esperado": None},
    {"frase": "un permiso para vender en la calle", "clase": "ambigua", "esperado": None},
    # --- no satisfacibles (controles negativos verificados: no existen en gob.bo) ---
    {"frase": "quiero sacar mi carnet", "clase": "no_satisfacible", "esperado": None},
    {"frase": "renovar mi carnet de identidad", "clase": "no_satisfacible", "esperado": None},
    {"frase": "mi cédula está vencida, dónde la renuevo", "clase": "no_satisfacible", "esperado": None},
    {"frase": "sacar pasaporte", "clase": "no_satisfacible", "esperado": None},
    {"frase": "cuánto cuesta el pasaporte y dónde lo saco", "clase": "no_satisfacible", "esperado": None},
    {"frase": "licencia de conducir por primera vez", "clase": "no_satisfacible", "esperado": None},
    {"frase": "renovar mi brevet", "clase": "no_satisfacible", "esperado": None},
    {"frase": "quiero casarme, qué necesito", "clase": "no_satisfacible", "esperado": None},
    {"frase": "bono Juana Azurduy", "clase": "no_satisfacible", "esperado": None},
    {"frase": "certificado de soltería", "clase": "no_satisfacible", "esperado": None},
    {"frase": "pagar mis multas de tránsito", "clase": "no_satisfacible", "esperado": None},
    {"frase": "sacar el SOAT de mi auto", "clase": "no_satisfacible", "esperado": None},
]
```

- [ ] **Step 2: Script de verificación de etiquetas contra la DB**

Crear `tests/verificar_eval.py`:

```python
"""Chequea que cada `esperado` de las directas exista en algún nombre de trámite de la DB.
Uso: venv/Scripts/python.exe tests/verificar_eval.py
Las directas cuyo esperado no exista deben reclasificarse (no_satisfacible) o eliminarse."""
import sys

from dotenv import load_dotenv

sys.path.insert(0, ".")

from db.connection import get_connection
from tests.eval_dataset import CASOS, normalizar


def main() -> None:
    load_dotenv()
    with get_connection() as conn:
        nombres = [normalizar(f[0]) for f in conn.execute("SELECT nombre FROM tramites").fetchall()]
    directas = [c for c in CASOS if c["clase"] == "directa"]
    faltantes = [
        c for c in directas if not any(normalizar(c["esperado"]) in n for n in nombres)
    ]
    print(f"directas: {len(directas)} | esperados sin trámite en la DB: {len(faltantes)}")
    for c in faltantes:
        print(f"  RECLASIFICAR/ELIMINAR: {c['frase']!r} (esperado {c['esperado']!r})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Correr la verificación y curar el dataset**

Run: `venv/Scripts/python.exe tests/verificar_eval.py`
Expected: lista de los `# VERIFICAR` que no existen. Acción: para cada faltante, decidir — si el trámite no existe en gob.bo, cambiar `clase` a `"no_satisfacible"` y `esperado` a `None` (y quitar el comentario); si existe con otro nombre, ajustar el substring `esperado` (buscar con `SELECT nombre FROM tramites WHERE nombre ILIKE '%...%'` vía `docker compose exec db psql -U ami -d ami -c "..."`). Quitar los comentarios `# VERIFICAR` restantes al confirmar.

- [ ] **Step 4: Script generador de variantes**

Crear `tests/generar_variantes.py`:

```python
"""Genera variantes coloquiales de las frases directas con el modelo potente.
Uso: venv/Scripts/python.exe tests/generar_variantes.py
Imprime literales Python para revisar A MANO y pegar en eval_dataset.py (curación obligatoria:
eliminar las que cambian el sentido o repiten una frase existente)."""
import sys

from dotenv import load_dotenv

sys.path.insert(0, ".")

from providers import factory
from tests.eval_dataset import CASOS

SISTEMA = """Sos un ciudadano boliviano común escribiendo a un chat de trámites del Estado.
Reformulá la frase dada en 2 variantes coloquiales distintas, como hablaría gente real
(informal, a veces con contexto personal, sin tecnicismos). Español de Bolivia.
Respondé SOLO las 2 variantes, una por línea, sin numeración ni comillas."""


def main() -> None:
    load_dotenv()
    chat = factory.chat_potente()
    for caso in [c for c in CASOS if c["clase"] == "directa"]:
        texto = chat.complete(
            system=SISTEMA,
            messages=[{"role": "user", "content": caso["frase"]}],
            max_tokens=200,
        )
        for linea in texto.strip().splitlines():
            frase = linea.strip().strip('"')
            if frase:
                print(f'    {{"frase": {frase!r}, "clase": "directa", "esperado": {caso["esperado"]!r}}},')


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Generar, curar y llegar a ≥60 frases**

Run: `venv/Scripts/python.exe tests/generar_variantes.py`
Acción: revisar la salida a mano, descartar variantes que cambian el sentido o duplican, y pegar las buenas en `CASOS` (sección "variantes generadas y curadas"). Objetivo: **≥60 casos totales**, con al menos 8 ambiguas y 10 no satisfacibles. Re-correr `tests/verificar_eval.py` → Expected: 0 faltantes.

- [ ] **Step 6: Adaptar el runner del eval al dataset nuevo**

Reemplazar en `tests/eval_retrieval.py` la lista `CASOS` y el `main()` (imports `unicodedata` y la función `_normalizar` se eliminan — ahora vienen del dataset):

```python
"""Eval manual de retrieval con frases coloquiales reales.

Uso: python tests/eval_retrieval.py
NO es un test de pytest: los MISS son información para calibrar, no fallas.
"""
import sys

from dotenv import load_dotenv

sys.path.insert(0, ".")

from api.confidence import evaluar_confianza
from db.connection import get_connection
from db.queries import buscar_tramites
from providers import factory
from tests.eval_dataset import CASOS, normalizar


def main() -> None:
    load_dotenv()
    emb = factory.embedder()
    hit1 = hit5 = directas = 0
    print(f"{'clase':16} {'frase':40} {'top-1':40} {'d1':>6} {'gap':>6} {'gate':8} resultado")
    with get_connection() as conn:
        for caso in CASOS:
            hits = buscar_tramites(conn, emb.embed_query(caso["frase"]), limit=5)
            distancias = [h["distancia"] for h in hits]
            veredicto = evaluar_confianza(distancias)
            estado = ""
            if caso["clase"] == "directa":
                directas += 1
                nombres = [normalizar(h["nombre"]) for h in hits]
                en1 = bool(nombres) and normalizar(caso["esperado"]) in nombres[0]
                en5 = any(normalizar(caso["esperado"]) in n for n in nombres)
                hit1 += en1
                hit5 += en5
                estado = "HIT@1" if en1 else ("HIT@5" if en5 else "MISS")
            d1 = distancias[0] if distancias else float("nan")
            gap = (distancias[1] - distancias[0]) if len(distancias) > 1 else float("nan")
            top = hits[0]["nombre"] if hits else "-"
            print(f"{caso['clase']:16} {caso['frase']:40.40} {top:40.40} {d1:6.3f} {gap:6.3f} {veredicto:8} {estado}")
    print(f"\ndirectas: hit@1 {hit1}/{directas}   hit@5 {hit5}/{directas}")
    print("Para el barrido de umbrales usar tests/calibrar_gate.py")


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Correr el eval y la suite**

Run: `venv/Scripts/python.exe tests/eval_retrieval.py` → Expected: tabla con ≥60 filas, hit@5 de directas visible.
Run: `venv/Scripts/python.exe -m pytest tests/ -q` → Expected: PASS (los scripts de eval no son tests de pytest).

- [ ] **Step 8: Commit**

```bash
git add tests/eval_dataset.py tests/generar_variantes.py tests/verificar_eval.py tests/eval_retrieval.py
git commit -m "feat: eval ampliado a 60+ frases etiquetadas en tres clases (Task 6 MVP)"
```

---

### Task 7: Script de calibración y umbrales nuevos

**Files:**
- Create: `tests/calibrar_gate.py`
- Modify: `api/confidence.py`

**Interfaces:**
- Consumes: `CASOS`/`normalizar` (Task 6), `evaluar_confianza` con "lejano" (Task 1).
- Produces: `UMBRAL_GAP` / `UMBRAL_DISTANCIA_MAX` recalibrados que todo el pipeline usa por default.

- [ ] **Step 1: Escribir el script de barrido**

Crear `tests/calibrar_gate.py`:

```python
"""Barrido de umbrales del gate sobre tests/eval_dataset.py.
Uso: venv/Scripts/python.exe tests/calibrar_gate.py
Una sola pasada de retrieval por frase; el barrido es matemática pura.
Criterio de elección: cero "claro incorrecto" primero; después maximizar
one-shot de directas + aclaración de ambiguas + gateo de no satisfacibles."""
import sys

from dotenv import load_dotenv

sys.path.insert(0, ".")

from api.confidence import UMBRAL_DISTANCIA_MAX, UMBRAL_GAP, evaluar_confianza
from db.connection import get_connection
from db.queries import buscar_tramites
from providers import factory
from tests.eval_dataset import CASOS, normalizar

GAPS = [round(0.005 * i, 3) for i in range(1, 21)]          # 0.005 .. 0.100 (gap 0 desactivaría la aclaración)
DISTS = [round(0.40 + 0.01 * i, 2) for i in range(0, 31)]   # 0.40 .. 0.70


def preparar() -> list[tuple[dict, list[float], bool]]:
    emb = factory.embedder()
    filas = []
    with get_connection() as conn:
        for caso in CASOS:
            hits = buscar_tramites(conn, emb.embed_query(caso["frase"]), limit=5)
            distancias = [h["distancia"] for h in hits]
            top1_ok = (
                caso["clase"] == "directa"
                and bool(hits)
                and normalizar(caso["esperado"]) in normalizar(hits[0]["nombre"])
            )
            filas.append((caso, distancias, top1_ok))
    return filas


def medir(filas, umbral_gap: float, umbral_dist: float) -> dict:
    m = {"directas": 0, "one_shot": 0, "claro_incorrecto": 0,
         "ambiguas": 0, "aclara_ok": 0, "negativas": 0, "gateadas": 0}
    for caso, distancias, top1_ok in filas:
        v = evaluar_confianza(distancias, umbral_gap, umbral_dist)
        if caso["clase"] == "directa":
            m["directas"] += 1
            if v == "claro" and top1_ok:
                m["one_shot"] += 1
            if v == "claro" and not top1_ok:
                m["claro_incorrecto"] += 1
        elif caso["clase"] == "ambigua":
            m["ambiguas"] += 1
            if v == "ambiguo":
                m["aclara_ok"] += 1
        else:
            m["negativas"] += 1
            if v in ("lejano", "vacio"):
                m["gateadas"] += 1
            if v == "claro":
                m["claro_incorrecto"] += 1
    return m


def main() -> None:
    load_dotenv()
    filas = preparar()
    resultados = []
    for g in GAPS:
        for d in DISTS:
            m = medir(filas, g, d)
            resultados.append((g, d, m))

    def puntaje(r):
        _, _, m = r
        return (m["claro_incorrecto"] == 0, m["one_shot"] + m["aclara_ok"] + m["gateadas"])

    resultados.sort(key=puntaje, reverse=True)
    print(f"{'gap':>6} {'dist':>6} {'one-shot':>9} {'claro_mal':>9} {'aclara_ok':>9} {'gateadas':>9}")
    for g, d, m in resultados[:15]:
        print(f"{g:6.3f} {d:6.2f} {m['one_shot']:>4}/{m['directas']:<4} {m['claro_incorrecto']:>9} "
              f"{m['aclara_ok']:>4}/{m['ambiguas']:<4} {m['gateadas']:>4}/{m['negativas']:<4}")
    actual = medir(filas, UMBRAL_GAP, UMBRAL_DISTANCIA_MAX)
    print(f"\numbrales actuales gap={UMBRAL_GAP} dist={UMBRAL_DISTANCIA_MAX}: {actual}")
    mejor = resultados[0]
    print(f"recomendado: gap={mejor[0]} dist={mejor[1]}")
    m = mejor[2]
    print(f"criterios spec: one-shot >= 75% de directas -> {m['one_shot']}/{m['directas']}; "
          f"claro incorrecto == 0 -> {m['claro_incorrecto']}; "
          f"negativas gateadas 100% -> {m['gateadas']}/{m['negativas']}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Correr el barrido**

Run: `venv/Scripts/python.exe tests/calibrar_gate.py`
Expected: tabla de 15 mejores combinaciones + recomendación. (Requiere DB cargada y `NVIDIA_API_KEY`; tarda ~2-3 min por los ~60-100 embeddings con rate limit.)

- [ ] **Step 3: Adoptar los umbrales ganadores**

En `api/confidence.py`, reemplazar los valores de `UMBRAL_GAP` y `UMBRAL_DISTANCIA_MAX` por los recomendados por el script (son datos de runtime — usar exactamente los del output) y reescribir el comentario de calibración citando: fecha, tamaño del eval, one-shot logrado, claro_incorrecto=0, y % de negativas gateadas. Verificar contra los criterios del spec: ≤25% de directas en aclaración innecesaria equivale a one-shot + claro_incorrecto ≥ 75% de directas; si ninguna combinación con claro_incorrecto=0 lo logra, elegir la mejor disponible y anotar la brecha en el comentario (se re-calibra tras la sesión con usuarios en la Task 18).

- [ ] **Step 4: Correr suite + eval**

Run: `venv/Scripts/python.exe -m pytest tests/ -q` → Expected: PASS (los tests de confidence pasan umbrales explícitos desde la Task 1).
Run: `venv/Scripts/python.exe tests/eval_retrieval.py` → Expected: la columna `gate` refleja los umbrales nuevos.

- [ ] **Step 5: Commit**

```bash
git add tests/calibrar_gate.py api/confidence.py
git commit -m "feat: barrido de calibracion y umbrales del gate recalibrados (Task 7 MVP)"
```

---

## Etapa 2 — Sync incremental

### Task 8: Columna `activo` + tabla `sync_state` + filtro en retrieval

**Files:**
- Modify: `db/schema.sql`
- Modify: `db/queries.py`
- Test: `tests/test_queries.py`

**Interfaces:**
- Produces: `leer_estado_tramites(conn) -> dict[int, dict]` (id → {"last_updated": date|None, "activo": bool}); `marcar_inactivos(conn, ids)`; `marcar_activos(conn, ids)`; `guardar_sync_state(conn)`; `buscar_tramites` excluye inactivos. Task 9 consume las cuatro funciones.

- [ ] **Step 1: Esquema**

Agregar al final de `db/schema.sql`:

```sql
ALTER TABLE tramites ADD COLUMN IF NOT EXISTS activo boolean NOT NULL DEFAULT true;

CREATE TABLE IF NOT EXISTS sync_state (
  id integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  last_sync date,
  updated_at timestamptz NOT NULL DEFAULT now()
);
```

Run: `docker compose exec -T db psql -U ami -d ami < db/schema.sql`
Expected: `ALTER TABLE` y `CREATE TABLE` sin errores.

- [ ] **Step 2: Tests de integración (fallan)**

Agregar al final de `tests/test_queries.py`:

```python
def test_inactivos_fuera_del_retrieval(conn):
    from db.queries import marcar_activos, marcar_inactivos

    guardar_tramite_completo(conn, _fila(900005, "TRAMITE DADO DE BAJA"), _vec(1.0))
    conn.commit()
    marcar_inactivos(conn, [900005])
    conn.commit()
    hits = [h for h in buscar_tramites(conn, _vec(1.0), limit=5000) if h["id"] == 900005]
    assert hits == []
    marcar_activos(conn, [900005])
    conn.commit()
    hits = [h for h in buscar_tramites(conn, _vec(1.0), limit=5000) if h["id"] == 900005]
    assert len(hits) == 1


def test_estado_tramites_y_sync_state(conn):
    from db.queries import guardar_sync_state, leer_estado_tramites

    guardar_tramite_completo(conn, _fila(900006, "PARA ESTADO"), _vec(0.5))
    conn.commit()
    estado = leer_estado_tramites(conn)
    assert estado[900006]["activo"] is True
    assert "last_updated" in estado[900006]

    guardar_sync_state(conn)
    conn.commit()
    fila = conn.execute("SELECT last_sync FROM sync_state WHERE id = 1").fetchone()
    assert fila[0] is not None
```

Run: `venv/Scripts/python.exe -m pytest tests/test_queries.py -v`
Expected: FAIL con ImportError.

- [ ] **Step 3: Implementar queries y filtro**

En `db/queries.py`, dentro de `_SQL_BUSCAR`, cambiar la línea `WHERE t.embedding IS NOT NULL` por:

```sql
WHERE t.embedding IS NOT NULL
  AND t.activo
```

y agregar al final del archivo:

```python
def leer_estado_tramites(conn) -> dict[int, dict]:
    filas = conn.execute("SELECT id, last_updated, activo FROM tramites").fetchall()
    return {f[0]: {"last_updated": f[1], "activo": f[2]} for f in filas}


def marcar_inactivos(conn, ids) -> None:
    conn.execute("UPDATE tramites SET activo = false WHERE id = ANY(%s)", (list(ids),))


def marcar_activos(conn, ids) -> None:
    conn.execute("UPDATE tramites SET activo = true WHERE id = ANY(%s)", (list(ids),))


def guardar_sync_state(conn) -> None:
    conn.execute(
        """
        INSERT INTO sync_state (id, last_sync, updated_at) VALUES (1, CURRENT_DATE, now())
        ON CONFLICT (id) DO UPDATE SET last_sync = EXCLUDED.last_sync, updated_at = now()
        """
    )
```

- [ ] **Step 4: Correr la suite completa**

Run: `venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add db/schema.sql db/queries.py tests/test_queries.py
git commit -m "feat: columna activo, sync_state y filtro de inactivos en retrieval (Task 8 MVP)"
```

---

### Task 9: `ingest/sync.py` — sync incremental semanal

**Files:**
- Create: `ingest/sync.py`
- Test: `tests/test_sync.py` (nuevo)
- Modify: `DECISIONS.md` (registro de la decisión sobre los CSVs)

**Interfaces:**
- Consumes: `leer_registros` (`ingest/load.py`), `mapear_tramite`/`parsear_fecha`/`texto_para_embedding` (`ingest/mapper.py`), `extraer_costo` (`ingest/costo_llm.py`), queries de la Task 8.
- Produces: función pura `diff_registros(registros, estado_db) -> tuple[list[dict], set[int]]` (cambiados, bajas); CLI `python -m ingest.sync [--dry-run] [--jsonl RUTA]`.

- [ ] **Step 1: Inspeccionar los CSVs reales y registrar la decisión**

Run (Bash):

```bash
curl -s https://raw.githubusercontent.com/datosbolivia/tramites-bo/main/adiciones.csv | head -5
curl -s https://raw.githubusercontent.com/datosbolivia/tramites-bo/main/modificaciones.csv | head -5
```

Expected: se ven las columnas reales. Acción: agregar al final de `DECISIONS.md` una sección "## Sync incremental: diff por fechaActualización (2026-07-14+)" registrando: (a) las columnas observadas de cada CSV, (b) que el diff implementado compara `fechaActualización` del jsonl contra `last_updated` de la DB porque cubre altas/modificaciones/bajas sin depender del formato de los CSVs (equivalencia ya sancionada en el spec como fallback), y (c) que si `fechaActualización` demostrara ser poco confiable, los CSVs quedan como alternativa documentada con sus columnas ya anotadas.

- [ ] **Step 2: Tests de la función pura de diff (fallan)**

Crear `tests/test_sync.py`:

```python
from datetime import date

from ingest.sync import diff_registros


def _registro(id_, fecha="01/01/2026"):
    return {"id": id_, "fechaActualización": fecha}


def _estado(last_updated, activo=True):
    return {"last_updated": last_updated, "activo": activo}


def test_registro_nuevo_entra_al_diff():
    cambiados, bajas = diff_registros([_registro(1)], {})
    assert [r["id"] for r in cambiados] == [1]
    assert bajas == set()


def test_registro_sin_cambios_no_entra():
    estado = {1: _estado(date(2026, 1, 1))}
    cambiados, _ = diff_registros([_registro(1, "01/01/2026")], estado)
    assert cambiados == []


def test_registro_modificado_entra():
    estado = {1: _estado(date(2026, 1, 1))}
    cambiados, _ = diff_registros([_registro(1, "08/02/2026")], estado)
    assert [r["id"] for r in cambiados] == [1]


def test_registro_en_db_sin_fecha_entra():
    estado = {1: _estado(None)}
    cambiados, _ = diff_registros([_registro(1)], estado)
    assert [r["id"] for r in cambiados] == [1]


def test_sin_fecha_en_ambos_lados_no_entra():
    # sin señal de cambio: no re-procesar cada semana (idempotencia)
    estado = {1: _estado(None)}
    cambiados, _ = diff_registros([_registro(1, fecha=None)], estado)
    assert cambiados == []


def test_baja_detectada_por_ausencia():
    estado = {1: _estado(date(2026, 1, 1)), 2: _estado(date(2026, 1, 1))}
    _, bajas = diff_registros([_registro(1, "01/01/2026")], estado)
    assert bajas == {2}


def test_inactivo_que_reaparece_entra_para_reactivarse():
    estado = {1: _estado(date(2026, 1, 1), activo=False)}
    cambiados, bajas = diff_registros([_registro(1, "01/01/2026")], estado)
    assert [r["id"] for r in cambiados] == [1]
    assert bajas == set()


def test_inactivo_ausente_no_es_baja_de_nuevo():
    estado = {1: _estado(date(2026, 1, 1), activo=False)}
    _, bajas = diff_registros([], estado)
    assert bajas == set()
```

Run: `venv/Scripts/python.exe -m pytest tests/test_sync.py -v`
Expected: FAIL con ModuleNotFoundError (`ingest.sync` no existe).

- [ ] **Step 3: Implementar `ingest/sync.py`**

```python
"""Sync incremental contra tramites-bo (el dataset se actualiza los domingos).

Uso:
    python -m ingest.sync             # descarga, aplica diff, upserta cambiados, marca bajas
    python -m ingest.sync --dry-run   # solo reporta qué haría
    python -m ingest.sync --jsonl ruta/local.jsonl

Agendable con cron / Task Scheduler; idempotente (correrlo dos veces no repite trabajo).
"""
import argparse
import logging
from datetime import date

from dotenv import load_dotenv

from db.connection import get_connection
from db.queries import (
    guardar_sync_state,
    guardar_tramite_completo,
    leer_estado_tramites,
    marcar_activos,
    marcar_inactivos,
)
from ingest.costo_llm import extraer_costo
from ingest.load import leer_registros
from ingest.mapper import mapear_tramite, parsear_fecha, texto_para_embedding
from providers import factory

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def diff_registros(registros: list[dict], estado_db: dict[int, dict]) -> tuple[list[dict], set[int]]:
    """Diff contra el estado de la DB. Devuelve (cambiados, bajas).

    - cambiados: ids nuevos, inactivos que reaparecen en el jsonl, o con
      fechaActualización posterior a last_updated (o last_updated nulo).
      Sin fecha parseable en el jsonl no hay señal de cambio: se salta
      (idempotencia — no re-procesar lo mismo cada semana).
    - bajas: ids activos en la DB que ya no están en el jsonl.
    """
    cambiados = []
    ids_jsonl: set[int] = set()
    for registro in registros:
        rid = int(registro["id"])
        ids_jsonl.add(rid)
        estado = estado_db.get(rid)
        fecha = parsear_fecha(registro.get("fechaActualización"))
        if estado is None or not estado["activo"]:
            cambiados.append(registro)
        elif fecha is not None and (estado["last_updated"] is None or fecha > estado["last_updated"]):
            cambiados.append(registro)
    bajas = {rid for rid, e in estado_db.items() if e["activo"] and rid not in ids_jsonl}
    return cambiados, bajas


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--jsonl", default=None, help="ruta local a tramites.jsonl (default: descarga)")
    parser.add_argument("--dry-run", action="store_true", help="solo reportar, sin escribir")
    args = parser.parse_args()

    registros = leer_registros(args.jsonl)
    with get_connection() as conn:
        estado_db = leer_estado_tramites(conn)
    cambiados, bajas = diff_registros(registros, estado_db)
    logger.info("jsonl: %d registros | cambiados: %d | bajas: %d", len(registros), len(cambiados), len(bajas))
    if args.dry_run:
        for r in cambiados[:20]:
            logger.info("  cambiado: %s %s", r["id"], r.get("nombre", "")[:60])
        for rid in sorted(bajas)[:20]:
            logger.info("  baja: %s", rid)
        return

    filas = []
    for registro in cambiados:
        try:
            filas.append(mapear_tramite(registro))
        except Exception:
            logger.exception("registro %s falló en el mapeo, se salta", registro.get("id"))

    if filas:
        chat = factory.chat_potente()
        for fila in filas:
            if not fila["necesita_llm"]:
                continue
            datos = extraer_costo(chat, fila["descripcion"], fila["resultado"])
            if datos:
                fila.update(
                    costo_monto=datos["monto"], costo_moneda=datos["moneda"], costo_concepto=datos["concepto"]
                )

        emb = factory.embedder()
        logger.info("generando %d embeddings...", len(filas))
        vectores = emb.embed_documents([texto_para_embedding(f) for f in filas])
    else:
        vectores = []

    guardadas = 0
    with get_connection() as conn:
        for fila, vector in zip(filas, vectores):
            try:
                guardar_tramite_completo(conn, fila, vector)
                marcar_activos(conn, [fila["id"]])
                conn.commit()
                guardadas += 1
            except Exception:
                conn.rollback()
                logger.exception("trámite %s falló al guardar, se salta", fila["id"])
        if bajas:
            marcar_inactivos(conn, bajas)
        guardar_sync_state(conn)
        conn.commit()
    logger.info("sync ok: %d/%d guardadas, %d bajas", guardadas, len(filas), len(bajas))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Correr los tests**

Run: `venv/Scripts/python.exe -m pytest tests/test_sync.py -v` → Expected: PASS (7 tests).
Run: `venv/Scripts/python.exe -m pytest tests/ -q` → Expected: PASS.

- [ ] **Step 5: Corrida real en dry-run**

Run: `venv/Scripts/python.exe -m ingest.sync --dry-run`
Expected: `jsonl: ~1739 registros | cambiados: N | bajas: M` con N chico (solo lo cambiado desde la carga del demo). Si N es enorme (≈todos), investigar `fechaActualización` vs `last_updated` antes de seguir (posible parseo de fecha roto) — no correr sin dry-run hasta entender el número.

- [ ] **Step 6: Corrida real y verificación**

Run: `venv/Scripts/python.exe -m ingest.sync`
Expected: `sync ok: N/N guardadas, M bajas`. Verificar: `docker compose exec db psql -U ami -d ami -c "SELECT last_sync FROM sync_state; SELECT count(*) FROM tramites WHERE NOT activo;"`

- [ ] **Step 7: Commit**

```bash
git add ingest/sync.py tests/test_sync.py DECISIONS.md
git commit -m "feat: sync incremental por fechaActualizacion con bajas y dry-run (Task 9 MVP)"
```

---

## Etapa 3 — Fetch en vivo

### Task 10: Módulo `api/live_fetch.py` + caché

**Files:**
- Create: `api/live_fetch.py`
- Modify: `db/schema.sql`, `db/queries.py`, `requirements.txt`
- Test: `tests/test_live_fetch.py` (nuevo), `tests/test_queries.py`

**Interfaces:**
- Consumes: `extraer_costo` (`ingest/costo_llm.py`), `ChatProvider`.
- Produces: `buscar_en_vivo(chat_potente, candidatos: list[dict]) -> dict | None` (dict: `{"url", "texto", "costo"}`); `primera_url(enlaces) -> str | None`; `extraer_texto(html) -> str`; queries `leer_fetch_cache(conn, url, ttl_dias=7)` / `guardar_fetch_cache(conn, url, datos)`. Task 11 consume `buscar_en_vivo`.

- [ ] **Step 1: Dependencia y esquema**

Agregar `beautifulsoup4>=4.12` al final de `requirements.txt` y correr:

Run: `venv/Scripts/python.exe -m pip install -r requirements.txt` → Expected: instala beautifulsoup4.

Agregar al final de `db/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS fetch_cache (
  url text PRIMARY KEY,
  datos jsonb NOT NULL,
  fetched_at timestamptz NOT NULL DEFAULT now()
);
```

Run: `docker compose exec -T db psql -U ami -d ami < db/schema.sql` → Expected: `CREATE TABLE`.

- [ ] **Step 2: Tests unitarios (fallan)**

Crear `tests/test_live_fetch.py`:

```python
from api.live_fetch import extraer_texto, primera_url


def test_primera_url_acepta_strings_y_dicts():
    assert primera_url(["https://a.gob.bo/x"]) == "https://a.gob.bo/x"
    assert primera_url([{"url": "https://b.gob.bo/y"}]) == "https://b.gob.bo/y"
    assert primera_url([{"enlace": "https://c.gob.bo/z"}]) == "https://c.gob.bo/z"
    assert primera_url([{"titulo": "sin url"}, "https://d.gob.bo"]) == "https://d.gob.bo"
    assert primera_url([]) is None
    assert primera_url(None) is None
    assert primera_url(["ftp://no-http"]) is None


def test_extraer_texto_limpia_html():
    html = """<html><head><style>body{color:red}</style></head>
    <body><nav>menu</nav><script>var x=1;</script>
    <p>Requisitos   del  trámite</p><p>Costo: 50 Bs</p></body></html>"""
    texto = extraer_texto(html)
    assert "Requisitos del trámite" in texto
    assert "Costo: 50 Bs" in texto
    assert "var x=1" not in texto
    assert "menu" not in texto


def test_extraer_texto_trunca():
    texto = extraer_texto("<p>" + "palabra " * 5000 + "</p>")
    assert len(texto) <= 8000
```

Run: `venv/Scripts/python.exe -m pytest tests/test_live_fetch.py -v`
Expected: FAIL con ModuleNotFoundError.

- [ ] **Step 3: Implementar el módulo**

Crear `api/live_fetch.py`:

```python
"""Fetch en vivo (paso online 4 del spec): cuando el retrieval no encuentra nada
usable, se trae la página oficial vía `enlaces` del registro más cercano y se
extrae con el mismo modelo potente y esquema que el paso offline. Fail-soft total."""
import logging

import httpx
from bs4 import BeautifulSoup

from db.connection import get_connection
from db.queries import guardar_fetch_cache, leer_fetch_cache
from ingest.costo_llm import extraer_costo
from providers.base import ChatProvider

logger = logging.getLogger(__name__)

TIMEOUT_SEGUNDOS = 10.0
MAX_BYTES = 2_000_000
MAX_CHARS_TEXTO = 8000
MIN_CHARS_TEXTO = 200
TTL_DIAS = 7


def primera_url(enlaces) -> str | None:
    for enlace in enlaces or []:
        if isinstance(enlace, str) and enlace.startswith("http"):
            return enlace
        if isinstance(enlace, dict):
            for clave in ("url", "enlace", "href", "link"):
                valor = enlace.get(clave)
                if isinstance(valor, str) and valor.startswith("http"):
                    return valor
    return None


def extraer_texto(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    texto = " ".join(soup.get_text(" ").split())
    return texto[:MAX_CHARS_TEXTO]


def fetch_pagina(url: str) -> str | None:
    """Fail-soft: None ante cualquier problema de red o tamaño."""
    try:
        with httpx.Client(timeout=TIMEOUT_SEGUNDOS, follow_redirects=True) as client:
            respuesta = client.get(url)
            respuesta.raise_for_status()
            if len(respuesta.content) > MAX_BYTES:
                return None
            return respuesta.text
    except Exception:
        logger.warning("fetch en vivo falló para %s", url, exc_info=True)
        return None


def buscar_en_vivo(chat_potente: ChatProvider, candidatos: list[dict]) -> dict | None:
    """Intenta responder desde la página externa del candidato más cercano con enlaces.

    Devuelve {"url", "texto", "costo"} o None. Fail-soft total: nunca lanza.
    """
    try:
        url = None
        for candidato in candidatos or []:
            url = primera_url(candidato.get("enlaces"))
            if url:
                break
        if not url:
            return None

        with get_connection() as conn:
            cacheado = leer_fetch_cache(conn, url, ttl_dias=TTL_DIAS)
        if cacheado is not None:
            return cacheado

        html = fetch_pagina(url)
        if not html:
            return None
        texto = extraer_texto(html)
        if len(texto) < MIN_CHARS_TEXTO:
            return None

        costo = extraer_costo(chat_potente, texto, None)
        datos = {"url": url, "texto": texto, "costo": costo}
        with get_connection() as conn:
            guardar_fetch_cache(conn, url, datos)
            conn.commit()
        return datos
    except Exception:
        logger.warning("buscar_en_vivo falló", exc_info=True)
        return None
```

Agregar al final de `db/queries.py`:

```python
def leer_fetch_cache(conn, url: str, ttl_dias: int = 7) -> dict | None:
    fila = conn.execute(
        "SELECT datos FROM fetch_cache WHERE url = %s AND fetched_at > now() - make_interval(days => %s)",
        (url, ttl_dias),
    ).fetchone()
    return fila[0] if fila else None


def guardar_fetch_cache(conn, url: str, datos: dict) -> None:
    conn.execute(
        """
        INSERT INTO fetch_cache (url, datos, fetched_at) VALUES (%s, %s, now())
        ON CONFLICT (url) DO UPDATE SET datos = EXCLUDED.datos, fetched_at = now()
        """,
        (url, Json(datos)),
    )
```

- [ ] **Step 4: Test de integración del caché**

Agregar al final de `tests/test_queries.py`:

```python
def test_fetch_cache_roundtrip_y_ttl(conn):
    from db.queries import guardar_fetch_cache, leer_fetch_cache

    guardar_fetch_cache(conn, "https://test.gob.bo/t", {"url": "https://test.gob.bo/t", "texto": "hola", "costo": None})
    conn.commit()
    assert leer_fetch_cache(conn, "https://test.gob.bo/t")["texto"] == "hola"
    conn.execute("UPDATE fetch_cache SET fetched_at = now() - interval '8 days' WHERE url = 'https://test.gob.bo/t'")
    conn.commit()
    assert leer_fetch_cache(conn, "https://test.gob.bo/t", ttl_dias=7) is None
    conn.execute("DELETE FROM fetch_cache WHERE url = 'https://test.gob.bo/t'")
    conn.commit()
```

- [ ] **Step 5: Correr la suite completa**

Run: `venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/live_fetch.py db/schema.sql db/queries.py requirements.txt tests/test_live_fetch.py tests/test_queries.py
git commit -m "feat: modulo de fetch en vivo con cache y fail-soft (Task 10 MVP)"
```

---

### Task 11: Integrar el fetch en vivo al pipeline

**Files:**
- Modify: `api/pipeline.py`, `api/prompts.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `buscar_en_vivo` (Task 10).
- Produces: `system_de_sintesis_en_vivo(datos_vivos: dict) -> str` en prompts; la rama vacio/lejano del pipeline intenta el fetch antes de rendirse. Se elimina el stub `fetch_live_fallback`.

- [ ] **Step 1: Tests (fallan)**

En `tests/test_pipeline.py` agregar:

```python
def test_lejano_con_fetch_vivo_streamea_respuesta(monkeypatch):
    _preparar(monkeypatch, [_hit(1, "CERCANO CON LINK", 0.80)])
    monkeypatch.setattr(
        pipeline, "buscar_en_vivo",
        lambda chat, candidatos: {"url": "https://x.gob.bo", "texto": "requisitos: CI", "costo": None},
    )
    deps = _deps()
    cid = deps.store.get_or_create(None)
    eventos = list(procesar_mensaje(deps, cid, "algo raro"))
    assert ("answer", {"delta": "Hola"}) in eventos
    assert eventos[-1] == ("answer", {"done": True, "tramite_ids": []})


def test_lejano_sin_fetch_vivo_cae_a_no_encontrado(monkeypatch):
    _preparar(monkeypatch, [_hit(1, "CERCANO", 0.80)])
    monkeypatch.setattr(pipeline, "buscar_en_vivo", lambda chat, candidatos: None)
    deps = _deps()
    cid = deps.store.get_or_create(None)
    eventos = list(procesar_mensaje(deps, cid, "algo raro"))
    assert "No encontré" in eventos[0][1]["delta"]
```

Run: `venv/Scripts/python.exe -m pytest tests/test_pipeline.py -v`
Expected: FAIL (no existe `pipeline.buscar_en_vivo`; el lejano responde no-encontrado directo).

- [ ] **Step 2: Prompt de síntesis en vivo**

Agregar al final de `api/prompts.py`:

```python
SISTEMA_SINTESIS_VIVO = """Sos AMI, asistente de trámites del Estado boliviano. La consulta del ciudadano no coincide con ningún trámite de la base de datos, pero se recuperó EN VIVO el contenido de una página oficial relacionada.

Reglas estrictas:
- Usá ÚNICAMENTE la información de <pagina>. No inventes requisitos, costos, plazos ni oficinas.
- Abrí la respuesta aclarando la fuente: que la información viene de la página oficial indicada en <url> y puede estar desactualizada.
- Si la página no responde la pregunta puntual, decilo y sugerí visitar la URL.
- Incluí la URL al final de la respuesta.
- Respondé en español claro, breve y accionable."""


def system_de_sintesis_en_vivo(datos_vivos: dict) -> str:
    partes = [SISTEMA_SINTESIS_VIVO, "\n\n<url>\n" + datos_vivos["url"] + "\n</url>"]
    if datos_vivos.get("costo"):
        partes.append("\n<costo_extraido>\n" + json.dumps(datos_vivos["costo"], ensure_ascii=False) + "\n</costo_extraido>")
    partes.append("\n<pagina>\n" + datos_vivos["texto"] + "\n</pagina>")
    return "".join(partes)
```

- [ ] **Step 3: Integrar al pipeline**

En `api/pipeline.py`:

- Eliminar la función stub `fetch_live_fallback`.
- Agregar imports: `from api.live_fetch import buscar_en_vivo` y `from api.prompts import ..., system_de_sintesis_en_vivo` (sumarlo a la línea de imports de prompts existente).
- Reemplazar la rama vacio/lejano por:

```python
        if veredicto in ("vacio", "lejano"):
            datos_vivos = buscar_en_vivo(deps.chat_potente, hits[:3])
            if datos_vivos is None:
                deps.store.append(conversation_id, "assistant", MENSAJE_NO_ENCONTRADO, tipo="not_found")
                _registrar(conversation_id, mensaje, consulta, filtros, hits, veredicto, "not_found")
                yield ("answer", {"delta": MENSAJE_NO_ENCONTRADO})
                yield ("answer", {"done": True, "tramite_ids": []})
                return
            partes = []
            for delta in deps.chat_potente.stream(
                system=system_de_sintesis_en_vivo(datos_vivos),
                messages=deps.store.mensajes(conversation_id),
                max_tokens=4096,
            ):
                partes.append(delta)
                yield ("answer", {"delta": delta})
            deps.store.append(conversation_id, "assistant", "".join(partes), tipo="answer")
            _registrar(conversation_id, mensaje, consulta, filtros, hits, veredicto, "answer")
            yield ("answer", {"done": True, "tramite_ids": []})
            return
```

- [ ] **Step 4: Correr la suite completa**

Run: `venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS. Nota: `test_sin_resultados_responde_no_encontrado` y `test_lejano_responde_no_encontrado` requieren que `buscar_en_vivo` devuelva None — con hits vacíos/sin enlaces reales eso ya pasa (los `_hit` de los tests tienen `enlaces: []` y con hits vacíos no hay candidatos), sin monkeypatch extra. Si algún test viejo llama a la red, monkeypatchear `pipeline.buscar_en_vivo` a `lambda *a: None` en ese test.

- [ ] **Step 5: Prueba manual E2E de la rama**

Con el server corriendo (`venv/Scripts/python.exe -m uvicorn api.main:app --port 8000`), mandar una consulta sin match (recordar: en Git Bash escribir el JSON a archivo UTF-8 y usar `curl --data-binary @archivo` por el bug de tildes inline):

```bash
printf '{"mensaje": "impuesto a las criptomonedas para empresas mineras"}' > /tmp/consulta.json
curl -s -N -X POST localhost:8000/chat -H "Content-Type: application/json" --data-binary @/tmp/consulta.json
```

Expected: o un `answer` citando la página oficial con URL, o el mensaje de no encontrado si el candidato no tiene enlaces — nunca un error.

- [ ] **Step 6: Commit**

```bash
git add api/pipeline.py api/prompts.py tests/test_pipeline.py
git commit -m "feat: fetch en vivo integrado a la rama vacio/lejano del pipeline (Task 11 MVP)"
```

---

## Etapa 4 — Conversación persistente

### Task 12: `PostgresConversationStore`

**Files:**
- Create: `api/conversations_pg.py`
- Modify: `db/schema.sql`, `api/main.py`
- Test: `tests/test_conversations_pg.py` (nuevo)

**Interfaces:**
- Consumes: interfaz de `ConversationStore` (Task 2).
- Produces: `PostgresConversationStore` con `get_or_create/append/mensajes/texto_de_consulta/contar_aclaraciones/limpiar_viejas(horas=24)`; `api/main.py` lo usa en `get_deps` (el in-memory queda para tests).

- [ ] **Step 1: Esquema**

Agregar al final de `db/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS conversaciones (
  id text PRIMARY KEY,
  mensajes jsonb NOT NULL DEFAULT '[]',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
```

Run: `docker compose exec -T db psql -U ami -d ami < db/schema.sql` → Expected: `CREATE TABLE`.

- [ ] **Step 2: Tests de integración (fallan)**

Crear `tests/test_conversations_pg.py`:

```python
"""Tests de integración: requieren `docker compose up -d` corriendo."""
import pytest

from api.conversations_pg import PostgresConversationStore
from db.connection import get_connection


@pytest.fixture()
def store():
    yield PostgresConversationStore()
    with get_connection() as conn:
        conn.execute("DELETE FROM conversaciones WHERE id LIKE 'test-pg-%'")
        conn.commit()


def test_roundtrip_sobrevive_a_otra_instancia(store):
    cid = store.get_or_create("test-pg-1")
    store.append(cid, "user", "papel del carro")
    store.append(cid, "assistant", "¿A o B?", tipo="clarification")

    otra = PostgresConversationStore()  # simula reinicio del proceso
    assert otra.mensajes(cid) == [
        {"role": "user", "content": "papel del carro"},
        {"role": "assistant", "content": "¿A o B?"},
    ]
    assert otra.texto_de_consulta(cid) == "papel del carro"
    assert otra.contar_aclaraciones(cid) == 1


def test_get_or_create_genera_id_si_falta(store):
    cid = store.get_or_create(None)
    assert cid
    with get_connection() as conn:
        conn.execute("DELETE FROM conversaciones WHERE id = %s", (cid,))
        conn.commit()


def test_limpiar_viejas(store):
    cid = store.get_or_create("test-pg-viejo")
    store.append(cid, "user", "hola")
    with get_connection() as conn:
        conn.execute("UPDATE conversaciones SET updated_at = now() - interval '25 hours' WHERE id = %s", (cid,))
        conn.commit()
    store.limpiar_viejas(horas=24)
    assert store.mensajes(cid) == []
```

Run: `venv/Scripts/python.exe -m pytest tests/test_conversations_pg.py -v`
Expected: FAIL con ModuleNotFoundError.

- [ ] **Step 3: Implementar**

Crear `api/conversations_pg.py`:

```python
import logging
import uuid

from psycopg.types.json import Json

from db.connection import get_connection

logger = logging.getLogger(__name__)


class PostgresConversationStore:
    """Historial persistente en la tabla `conversaciones`. Misma interfaz que
    ConversationStore (in-memory, que queda para tests)."""

    def get_or_create(self, conversation_id: str | None) -> str:
        cid = conversation_id or str(uuid.uuid4())
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO conversaciones (id) VALUES (%s) ON CONFLICT (id) DO NOTHING", (cid,)
            )
            conn.commit()
        return cid

    def append(self, conversation_id: str, role: str, content: str, tipo: str | None = None) -> None:
        mensaje = {"role": role, "content": content, "tipo": tipo or role}
        with get_connection() as conn:
            conn.execute(
                "UPDATE conversaciones SET mensajes = mensajes || %s::jsonb, updated_at = now() WHERE id = %s",
                (Json([mensaje]), conversation_id),
            )
            conn.commit()

    def _crudos(self, conversation_id: str) -> list[dict]:
        with get_connection() as conn:
            fila = conn.execute(
                "SELECT mensajes FROM conversaciones WHERE id = %s", (conversation_id,)
            ).fetchone()
        return fila[0] if fila else []

    def mensajes(self, conversation_id: str) -> list[dict]:
        return [{"role": m["role"], "content": m["content"]} for m in self._crudos(conversation_id)]

    def texto_de_consulta(self, conversation_id: str) -> str:
        return " ".join(m["content"] for m in self._crudos(conversation_id) if m["role"] == "user")

    def contar_aclaraciones(self, conversation_id: str) -> int:
        return sum(1 for m in self._crudos(conversation_id) if m.get("tipo") == "clarification")

    def limpiar_viejas(self, horas: int = 24) -> None:
        """Best-effort: se llama al armar las deps; un fallo no impide arrancar."""
        try:
            with get_connection() as conn:
                conn.execute(
                    "DELETE FROM conversaciones WHERE updated_at < now() - make_interval(hours => %s)",
                    (horas,),
                )
                conn.commit()
        except Exception:
            logger.warning("no se pudieron limpiar conversaciones viejas", exc_info=True)
```

- [ ] **Step 4: Wiring en `api/main.py`**

Reemplazar el import y la construcción del store en `get_deps`:

```python
from api.conversations_pg import PostgresConversationStore
```

(reemplaza `from api.conversations import ConversationStore`) y en `get_deps`:

```python
        store = PostgresConversationStore()
        store.limpiar_viejas(horas=24)
        _deps = Deps(
            chat_economico=factory.chat_economico(),
            chat_potente=factory.chat_potente(),
            embedder=factory.embedder(),
            store=store,
            catalogos=catalogos,
        )
```

(`tests/test_api.py` no se ve afectado: construye sus `Deps` fake con el store in-memory.)

- [ ] **Step 5: Correr la suite y prueba manual**

Run: `venv/Scripts/python.exe -m pytest tests/ -q` → Expected: PASS.
Prueba manual: levantar el server, mandar una consulta ambigua, **reiniciar el server**, responder la aclaración con el mismo `conversation_id` → la conversación continúa (antes se perdía).

- [ ] **Step 6: Commit**

```bash
git add api/conversations_pg.py db/schema.sql api/main.py tests/test_conversations_pg.py
git commit -m "feat: conversaciones persistentes en Postgres con limpieza a 24h (Task 12 MVP)"
```

---

## Etapa 5 — Trámites relacionados

### Task 13: Tabla + candidatos + validación con muestra

**Files:**
- Modify: `db/schema.sql`, `db/queries.py`, `api/prompts.py`
- Create: `ingest/relacionados.py`
- Test: `tests/test_queries.py`, `tests/test_relaciones.py` (nuevo)

**Interfaces:**
- Produces: tabla `tramites_relacionados`; `candidatos_relacionados(conn, tramite_id, limit=5) -> list[dict]`; `guardar_relaciones(conn, tramite_id, relaciones)`; `listar_relacionados(conn, tramite_id, limit=3)`; prompts `SISTEMA_RELACIONES`, `schema_relaciones(ids)`, `usuario_relaciones(base, candidatos)`; CLI `python -m ingest.relacionados --muestra 20`. Task 14 corre el batch completo; Task 15 consume `listar_relacionados`.

- [ ] **Step 1: Esquema**

Agregar al final de `db/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS tramites_relacionados (
  tramite_id integer REFERENCES tramites(id) ON DELETE CASCADE,
  related_tramite_id integer REFERENCES tramites(id) ON DELETE CASCADE,
  tipo_relacion text NOT NULL CHECK (tipo_relacion IN ('siguiente_paso', 'requisito_previo', 'alternativa', 'mismo_evento')),
  PRIMARY KEY (tramite_id, related_tramite_id)
);
```

Run: `docker compose exec -T db psql -U ami -d ami < db/schema.sql` → Expected: `CREATE TABLE`.

- [ ] **Step 2: Tests de queries (fallan)**

Agregar al final de `tests/test_queries.py`:

```python
def test_candidatos_y_relaciones(conn):
    from db.queries import candidatos_relacionados, guardar_relaciones, listar_relacionados

    # misma entidad (segip-test) => candidatos entre sí
    guardar_tramite_completo(conn, _fila(900010, "TRAMITE BASE"), _vec(1.0))
    guardar_tramite_completo(conn, _fila(900011, "TRAMITE VECINO"), _vec(0.9))
    conn.commit()

    candidatos = candidatos_relacionados(conn, 900010, limit=5)
    assert any(c["id"] == 900011 for c in candidatos)

    guardar_relaciones(conn, 900010, [
        {"id": 900011, "tipo": "siguiente_paso"},
        {"id": 900011, "tipo": "ninguna"},  # ninguna no se persiste (y el PK evita duplicar)
    ])
    conn.commit()
    relacionados = listar_relacionados(conn, 900010)
    assert relacionados == [{"tipo": "siguiente_paso", "nombre": "TRAMITE VECINO", "entidad_nombre": "SEGIP TEST"}]
```

Run: `venv/Scripts/python.exe -m pytest tests/test_queries.py::test_candidatos_y_relaciones -v`
Expected: FAIL con ImportError.

- [ ] **Step 3: Implementar queries**

Agregar al final de `db/queries.py`:

```python
def candidatos_relacionados(conn, tramite_id: int, limit: int = 5) -> list[dict]:
    """Candidatos baratos: misma entidad o evento de vida compartido, por cercanía de embedding."""
    filas = conn.execute(
        """
        SELECT t.id, t.nombre, t.descripcion, e.nombre AS entidad_nombre,
               t.embedding <=> b.embedding AS distancia
        FROM tramites b
        JOIN tramites t ON t.id <> b.id AND t.embedding IS NOT NULL AND t.activo
        LEFT JOIN entidades e ON e.id = t.entidad_id
        WHERE b.id = %(id)s AND b.embedding IS NOT NULL
          AND (t.entidad_id = b.entidad_id
               OR EXISTS (
                    SELECT 1 FROM tramites_eventos te1
                    JOIN tramites_eventos te2 ON te2.evento_id = te1.evento_id
                    WHERE te1.tramite_id = b.id AND te2.tramite_id = t.id))
        ORDER BY t.embedding <=> b.embedding
        LIMIT %(limit)s
        """,
        {"id": tramite_id, "limit": limit},
    ).fetchall()
    return [
        {"id": f[0], "nombre": f[1], "descripcion": f[2], "entidad_nombre": f[3], "distancia": float(f[4])}
        for f in filas
    ]


def guardar_relaciones(conn, tramite_id: int, relaciones: list[dict]) -> None:
    conn.execute("DELETE FROM tramites_relacionados WHERE tramite_id = %s", (tramite_id,))
    for relacion in relaciones:
        if relacion["tipo"] == "ninguna":
            continue
        conn.execute(
            """
            INSERT INTO tramites_relacionados (tramite_id, related_tramite_id, tipo_relacion)
            VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
            """,
            (tramite_id, relacion["id"], relacion["tipo"]),
        )


def listar_relacionados(conn, tramite_id: int, limit: int = 3) -> list[dict]:
    filas = conn.execute(
        """
        SELECT tr.tipo_relacion, t.nombre, e.nombre
        FROM tramites_relacionados tr
        JOIN tramites t ON t.id = tr.related_tramite_id AND t.activo
        LEFT JOIN entidades e ON e.id = t.entidad_id
        WHERE tr.tramite_id = %s
        LIMIT %s
        """,
        (tramite_id, limit),
    ).fetchall()
    return [{"tipo": f[0], "nombre": f[1], "entidad_nombre": f[2]} for f in filas]
```

Nota: `guardar_relaciones` con tipo `"ninguna"` la salta ANTES del INSERT, así el CHECK de la tabla no se dispara.

- [ ] **Step 4: Prompts de clasificación + tests**

Crear `tests/test_relaciones.py`:

```python
from api.prompts import schema_relaciones, usuario_relaciones


def test_schema_restringe_ids_y_tipos():
    schema = schema_relaciones([10, 20])
    item = schema["properties"]["relaciones"]["items"]
    assert item["properties"]["id"]["enum"] == [10, 20]
    assert "ninguna" in item["properties"]["tipo"]["enum"]


def test_usuario_relaciones_incluye_base_y_candidatos():
    base = {"id": 1, "nombre": "REGISTRO DE COMERCIO", "descripcion": "x" * 500}
    candidatos = [{"id": 2, "nombre": "NIT", "descripcion": "obtener nit"}]
    texto = usuario_relaciones(base, candidatos)
    assert "REGISTRO DE COMERCIO" in texto
    assert "[2] NIT" in texto
    assert len(texto) < 2500  # descripciones truncadas a 300 chars
```

Run: `venv/Scripts/python.exe -m pytest tests/test_relaciones.py -v` → Expected: FAIL con ImportError.

Agregar al final de `api/prompts.py`:

```python
SISTEMA_RELACIONES = """Sos un experto en trámites del Estado boliviano. Dado un trámite BASE y una lista de trámites CANDIDATOS, clasificá la relación procedimental de cada candidato respecto al base:
- siguiente_paso: el ciudadano típicamente hace el candidato DESPUÉS del base.
- requisito_previo: el candidato se necesita ANTES de poder hacer el base.
- alternativa: resuelven la misma necesidad por vías distintas.
- mismo_evento: pertenecen al mismo momento de vida pero sin orden entre sí.
- ninguna: sin relación procedimental útil (similitud solo temática o superficial).
Basate únicamente en los nombres y descripciones. Ante la duda, "ninguna"."""


def schema_relaciones(ids: list[int]) -> dict:
    return {
        "type": "object",
        "properties": {
            "relaciones": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "enum": ids},
                        "tipo": {
                            "type": "string",
                            "enum": ["siguiente_paso", "requisito_previo", "alternativa", "mismo_evento", "ninguna"],
                        },
                    },
                    "required": ["id", "tipo"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["relaciones"],
        "additionalProperties": False,
    }


def _resumen(tramite: dict) -> str:
    descripcion = (tramite.get("descripcion") or "")[:300]
    return f"{tramite['nombre']} — {descripcion}"


def usuario_relaciones(base: dict, candidatos: list[dict]) -> str:
    lineas = [f"[{c['id']}] {_resumen(c)}" for c in candidatos]
    return "BASE:\n" + _resumen(base) + "\n\nCANDIDATOS:\n" + "\n".join(lineas)
```

- [ ] **Step 5: Script con modo muestra**

Crear `ingest/relacionados.py`:

```python
"""Clasifica relaciones procedimentales entre trámites con el modelo potente.

Uso:
    python -m ingest.relacionados --muestra 20   # imprime 20 para validar A MANO, no escribe
    python -m ingest.relacionados                # corre todo y persiste (~30-45 min por rate limit)
    python -m ingest.relacionados --desde 5000   # retoma desde un id (corridas interrumpidas)
"""
import argparse
import logging
import time

from dotenv import load_dotenv

from api.prompts import SISTEMA_RELACIONES, schema_relaciones, usuario_relaciones
from db.connection import get_connection
from db.queries import candidatos_relacionados, guardar_relaciones
from providers import factory

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PAUSA_SEGUNDOS = 1.6  # free tier NIM ~40 req/min


def clasificar_tramite(chat, base: dict, candidatos: list[dict]) -> list[dict]:
    """Fail-open: lista vacía si el modelo no coopera."""
    if not candidatos:
        return []
    ids_validos = [c["id"] for c in candidatos]
    datos = chat.complete_json(
        system=SISTEMA_RELACIONES,
        messages=[{"role": "user", "content": usuario_relaciones(base, candidatos)}],
        schema=schema_relaciones(ids_validos),
        max_tokens=500,
    )
    if not datos or not isinstance(datos.get("relaciones"), list):
        return []
    return [
        r for r in datos["relaciones"]
        if isinstance(r, dict) and r.get("id") in ids_validos
        and r.get("tipo") in ("siguiente_paso", "requisito_previo", "alternativa", "mismo_evento", "ninguna")
    ]


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--muestra", type=int, default=None, help="clasificar N trámites e imprimir, sin escribir")
    parser.add_argument("--desde", type=int, default=0, help="retomar desde este tramite_id")
    args = parser.parse_args()

    chat = factory.chat_potente()
    with get_connection() as conn:
        bases = conn.execute(
            "SELECT id, nombre, descripcion FROM tramites "
            "WHERE activo AND embedding IS NOT NULL AND id >= %s ORDER BY id",
            (args.desde,),
        ).fetchall()
    bases = [{"id": f[0], "nombre": f[1], "descripcion": f[2]} for f in bases]
    if args.muestra:
        bases = bases[: args.muestra]

    procesados = con_relacion = 0
    with get_connection() as conn:
        for base in bases:
            candidatos = candidatos_relacionados(conn, base["id"], limit=5)
            relaciones = clasificar_tramite(chat, base, candidatos)
            utiles = [r for r in relaciones if r["tipo"] != "ninguna"]
            if args.muestra:
                nombres = {c["id"]: c["nombre"] for c in candidatos}
                print(f"\nBASE [{base['id']}] {base['nombre']}")
                for r in relaciones:
                    print(f"  {r['tipo']:16} -> [{r['id']}] {nombres.get(r['id'], '?')}")
            else:
                try:
                    guardar_relaciones(conn, base["id"], relaciones)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    logger.exception("fallo guardando relaciones de %s", base["id"])
            procesados += 1
            con_relacion += bool(utiles)
            if procesados % 50 == 0:
                logger.info("procesados %d/%d (%d con alguna relación)", procesados, len(bases), con_relacion)
            time.sleep(PAUSA_SEGUNDOS)
    logger.info("listo: %d procesados, %d con alguna relación útil", procesados, con_relacion)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Correr suite + validación con muestra**

Run: `venv/Scripts/python.exe -m pytest tests/ -q` → Expected: PASS.
Run: `venv/Scripts/python.exe -m ingest.relacionados --muestra 20`
Expected: 20 bloques BASE→relaciones. **Gate de validación (paso obligatorio del spec):** revisar a mano si las relaciones tienen sentido procedimental (no solo temático). Si la mayoría son basura, ajustar `SISTEMA_RELACIONES` (ej. exigir más "ninguna") o la heurística de candidatos, y repetir la muestra ANTES de la Task 14. Anotar el veredicto de la validación en `DECISIONS.md`.

- [ ] **Step 7: Commit**

```bash
git add db/schema.sql db/queries.py api/prompts.py ingest/relacionados.py tests/test_queries.py tests/test_relaciones.py DECISIONS.md
git commit -m "feat: candidatos y clasificacion de tramites relacionados con validacion (Task 13 MVP)"
```

---

### Task 14: Corrida batch completa de relaciones

**Files:**
- Modify: ninguno (ejecución del script de la Task 13)

**Interfaces:**
- Consumes: `python -m ingest.relacionados` (Task 13, ya validado con muestra).
- Produces: tabla `tramites_relacionados` poblada para toda la DB.

- [ ] **Step 1: Corrida completa**

Run: `venv/Scripts/python.exe -m ingest.relacionados`
Expected: ~1,700 procesados en 30-60 min (rate limit NIM). Si se corta, retomar con `--desde <último id logueado>`.

- [ ] **Step 2: Verificación**

Run: `docker compose exec db psql -U ami -d ami -c "SELECT tipo_relacion, count(*) FROM tramites_relacionados GROUP BY 1; SELECT count(DISTINCT tramite_id) FROM tramites_relacionados;"`
Expected: distribución razonable entre los 4 tipos (sin que un tipo domine >90%) y una fracción significativa de trámites con relaciones. Muestrear 5 filas al azar y validarlas a ojo:
`docker compose exec db psql -U ami -d ami -c "SELECT t1.nombre, tr.tipo_relacion, t2.nombre FROM tramites_relacionados tr JOIN tramites t1 ON t1.id=tr.tramite_id JOIN tramites t2 ON t2.id=tr.related_tramite_id ORDER BY random() LIMIT 5;"`

- [ ] **Step 3: Commit (si hubo ajustes)**

Si la corrida obligó a tocar prompt/heurística, commitear esos ajustes:

```bash
git add -A
git commit -m "feat: relaciones procedimentales pobladas para toda la DB (Task 14 MVP)"
```

---

### Task 15: Relacionados en la síntesis

**Files:**
- Modify: `api/prompts.py`, `api/pipeline.py`
- Test: `tests/test_pipeline.py`, `tests/test_prompts.py`

**Interfaces:**
- Consumes: `listar_relacionados` (Task 13).
- Produces: `system_de_sintesis(tramite, alternativas=None, relacionados=None)` — firma final.

- [ ] **Step 1: Tests (fallan)**

Agregar a `tests/test_prompts.py`:

```python
def test_sintesis_incluye_relacionados():
    system = system_de_sintesis(
        _tramite(),
        relacionados=[{"tipo": "siguiente_paso", "nombre": "TRAMITE Y", "entidad_nombre": "ENT"}],
    )
    assert "TRAMITE Y" in system
    assert "siguiente_paso" in system
```

Agregar a `tests/test_pipeline.py`:

```python
def test_respuesta_clara_incluye_relacionados_en_prompt(monkeypatch):
    _preparar(monkeypatch, [_hit(1, "CEDULA", 0.2), _hit(2, "OTRO", 0.5)])
    monkeypatch.setattr(
        pipeline, "listar_relacionados",
        lambda conn, tid, limit=3: [{"tipo": "siguiente_paso", "nombre": "PASO SIGUIENTE", "entidad_nombre": None}],
    )
    capturado = {}

    class ChatEspia(FakeChat):
        def stream(self, *, system, messages, max_tokens=4096):
            capturado["system"] = system
            yield "ok"

    deps = _deps(potente=ChatEspia())
    cid = deps.store.get_or_create(None)
    list(procesar_mensaje(deps, cid, "carnet"))
    assert "PASO SIGUIENTE" in capturado["system"]


def test_relacionados_roto_no_rompe_respuesta(monkeypatch):
    _preparar(monkeypatch, [_hit(1, "CEDULA", 0.2), _hit(2, "OTRO", 0.5)])

    def explota(conn, tid, limit=3):
        raise RuntimeError("tabla caída")

    monkeypatch.setattr(pipeline, "listar_relacionados", explota)
    deps = _deps()
    cid = deps.store.get_or_create(None)
    eventos = list(procesar_mensaje(deps, cid, "carnet"))
    assert eventos[-1] == ("answer", {"done": True, "tramite_ids": [1]})
```

Run: `venv/Scripts/python.exe -m pytest tests/test_prompts.py tests/test_pipeline.py -v`
Expected: FAIL (firma y atributo inexistentes).

- [ ] **Step 2: Extender el prompt**

En `api/prompts.py`, reemplazar `system_de_sintesis` por la versión final:

```python
def system_de_sintesis(
    tramite: dict,
    alternativas: list[dict] | None = None,
    relacionados: list[dict] | None = None,
) -> str:
    datos = {k: v for k, v in tramite.items() if k != "distancia"}
    base = SISTEMA_SINTESIS + "\n\n<tramite>\n" + json.dumps(datos, ensure_ascii=False, default=str) + "\n</tramite>"
    if alternativas:
        lineas = "\n".join(
            f"- {a['nombre']} ({a.get('entidad_nombre') or 'entidad desconocida'})" for a in alternativas
        )
        base += (
            "\n\nAtención: la coincidencia con la consulta NO es segura. Abrí la respuesta aclarando qué "
            'trámite estás mostrando (ej. "Te muestro el que mejor coincide con tu consulta: ...") y cerrá '
            "mencionando en una línea estas alternativas por si buscaba otra cosa:\n" + lineas
        )
    if relacionados:
        lineas = "\n".join(
            f"- ({r['tipo']}) {r['nombre']}" + (f" — {r['entidad_nombre']}" if r.get("entidad_nombre") else "")
            for r in relacionados
        )
        base += (
            "\n\nTrámites relacionados (si alguno es pertinente a la consulta, mencionalo en UNA línea al final "
            "como anticipación — ej. \"después de esto probablemente necesites...\"; no inventes detalles de ellos):\n"
            + lineas
        )
    return base
```

- [ ] **Step 3: Integrar al pipeline**

En `api/pipeline.py`: agregar `listar_relacionados` al import de `db.queries`, y en la rama de respuesta (después de `top = hits[0]` y antes del stream):

```python
        relacionados: list[dict] = []
        try:
            with get_connection() as conn:
                relacionados = listar_relacionados(conn, top["id"])
        except Exception:
            logger.warning("no se pudieron leer relacionados de %s", top["id"], exc_info=True)
```

y pasar `relacionados=relacionados or None` en la llamada a `system_de_sintesis`.

- [ ] **Step 4: Correr la suite completa**

Run: `venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/prompts.py api/pipeline.py tests/test_pipeline.py tests/test_prompts.py
git commit -m "feat: tramites relacionados anticipados en la sintesis (Task 15 MVP)"
```

---

## Etapa 6 — Providers open-source (opcionales, NIM sigue default)

### Task 16: Provider Ollama + embeddings sentence-transformers

**Files:**
- Create: `providers/st_embeddings.py`, `requirements-oss.txt`
- Modify: `providers/factory.py`, `.env.example`
- Test: `tests/test_providers.py`, `tests/test_st_embeddings.py` (nuevo)

**Interfaces:**
- Produces: `PROVIDER=ollama` en el factory (chat vía `OpenAICompatChatProvider` → `http://localhost:11434/v1`; embeddings vía `SentenceTransformersEmbeddingProvider`); clase `SentenceTransformersEmbeddingProvider(model_name="intfloat/multilingual-e5-base", model=None)` con la interfaz `EmbeddingProvider`. Task 17 la consume.

- [ ] **Step 1: Tests (fallan)**

Crear `tests/test_st_embeddings.py`:

```python
from providers.st_embeddings import SentenceTransformersEmbeddingProvider


class FakeSTModel:
    """Evita depender de torch en la suite: el modelo real es dependencia opcional."""

    def __init__(self):
        self.llamadas = []

    def encode(self, texts, normalize_embeddings=False):
        self.llamadas.append(texts)
        return [[0.1, 0.2, 0.3] for _ in texts]


def test_prefijos_e5_y_conversion_a_float():
    fake = FakeSTModel()
    provider = SentenceTransformersEmbeddingProvider(model=fake)
    docs = provider.embed_documents(["hola", "chau"])
    assert docs == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert fake.llamadas[0] == ["passage: hola", "passage: chau"]

    q = provider.embed_query("carnet")
    assert q == [0.1, 0.2, 0.3]
    assert fake.llamadas[1] == ["query: carnet"]
```

Agregar a `tests/test_providers.py`:

```python
def test_factory_ollama_usa_openai_compat(monkeypatch):
    from providers import factory

    monkeypatch.setenv("PROVIDER", "ollama")
    monkeypatch.delenv("MODELO_POTENTE", raising=False)
    monkeypatch.delenv("MODELO_ECONOMICO", raising=False)
    potente = factory.chat_potente()
    economico = factory.chat_economico()
    assert potente.model == "llama3.1:8b"
    assert economico.model == "llama3.1:8b"
```

Run: `venv/Scripts/python.exe -m pytest tests/test_st_embeddings.py tests/test_providers.py -v`
Expected: FAIL (módulo y rama del factory inexistentes).

- [ ] **Step 2: Implementar el provider de embeddings**

Crear `providers/st_embeddings.py`:

```python
class SentenceTransformersEmbeddingProvider:
    """Embeddings locales (dependencia opcional: pip install -r requirements-oss.txt).

    Los modelos e5 REQUIEREN los prefijos "query:"/"passage:" — sin ellos la calidad
    de retrieval se degrada silenciosamente.
    """

    def __init__(self, model_name: str = "intfloat/multilingual-e5-base", model=None):
        if model is None:
            from sentence_transformers import SentenceTransformer  # import perezoso: torch es pesado

            model = SentenceTransformer(model_name)
        self._model = model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectores = self._model.encode([f"passage: {t}" for t in texts], normalize_embeddings=True)
        return [list(map(float, v)) for v in vectores]

    def embed_query(self, text: str) -> list[float]:
        vector = self._model.encode([f"query: {text}"], normalize_embeddings=True)[0]
        return list(map(float, vector))
```

Crear `requirements-oss.txt`:

```
# Dependencias opcionales para providers open-source y eval comparativo (Etapa 6)
sentence-transformers>=3.0
numpy>=1.26
```

- [ ] **Step 3: Extender el factory**

En `providers/factory.py`, agregar tras `_nvidia_chat`:

```python
def _ollama_chat(modelo: str) -> OpenAICompatChatProvider:
    return OpenAICompatChatProvider(
        model=modelo,
        base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        api_key=os.environ.get("OLLAMA_API_KEY", "ollama"),
    )
```

y en cada función, la rama ollama:

```python
def chat_potente() -> ChatProvider:
    if _proveedor() == "anthropic":
        return AnthropicChatProvider(os.environ.get("MODELO_POTENTE", "claude-sonnet-5"))
    if _proveedor() == "ollama":
        return _ollama_chat(os.environ.get("MODELO_POTENTE", "llama3.1:8b"))
    return _nvidia_chat(os.environ.get("MODELO_POTENTE", "meta/llama-3.3-70b-instruct"))


def chat_economico() -> ChatProvider:
    if _proveedor() == "anthropic":
        return AnthropicChatProvider(os.environ.get("MODELO_ECONOMICO", "claude-haiku-4-5"))
    if _proveedor() == "ollama":
        return _ollama_chat(os.environ.get("MODELO_ECONOMICO", "llama3.1:8b"))
    return _nvidia_chat(os.environ.get("MODELO_ECONOMICO", "meta/llama-3.1-8b-instruct"))


def embedder() -> EmbeddingProvider:
    if _proveedor() == "anthropic":
        return VoyageEmbeddingProvider(
            model=os.environ.get("MODELO_EMBEDDINGS", "voyage-4-lite"),
            output_dimension=int(os.environ.get("EMBEDDING_DIM", "1024")),
        )
    if _proveedor() == "ollama":
        from providers.st_embeddings import SentenceTransformersEmbeddingProvider  # opcional

        return SentenceTransformersEmbeddingProvider(
            os.environ.get("MODELO_EMBEDDINGS", "intfloat/multilingual-e5-base")
        )
    return OpenAICompatEmbeddingProvider(
        model=os.environ.get("MODELO_EMBEDDINGS", "baai/bge-m3"),
        base_url=os.environ.get("NVIDIA_BASE_URL", _NVIDIA_BASE_URL_DEFAULT),
        api_key=os.environ.get("NVIDIA_API_KEY", ""),
    )
```

Agregar a `.env.example` (sección comentada):

```
# Providers open-source (opcional, requiere: pip install -r requirements-oss.txt y Ollama corriendo)
# PROVIDER=ollama
# OLLAMA_BASE_URL=http://localhost:11434/v1
# MODELO_POTENTE=llama3.1:8b        # o qwen2.5:14b si el hardware da
# MODELO_ECONOMICO=llama3.1:8b
# MODELO_EMBEDDINGS=intfloat/multilingual-e5-base
```

- [ ] **Step 4: Correr la suite completa**

Run: `venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS sin tener torch instalado (el import de sentence-transformers es perezoso y los tests usan el fake). IMPORTANTE: `PROVIDER=ollama` NO cambia el default — verificar que sin la env var todo sigue en NIM.

- [ ] **Step 5: Commit**

```bash
git add providers/st_embeddings.py providers/factory.py requirements-oss.txt .env.example tests/test_providers.py tests/test_st_embeddings.py
git commit -m "feat: providers opcionales ollama y sentence-transformers, NIM sigue default (Task 16 MVP)"
```

---

### Task 17: Eval comparativo offline (embeddings + síntesis)

**Files:**
- Create: `tests/eval_comparativo.py`
- Create: `docs/evals/` (directorio para los reportes)

**Interfaces:**
- Consumes: `CASOS`/`normalizar` (Task 6), `evaluar_confianza` (Task 1), `SentenceTransformersEmbeddingProvider` (Task 16), `texto_para_embedding` (`ingest/mapper.py`).
- Produces: reporte por consola (embeddings) y `docs/evals/sintesis-comparada.md` (síntesis). NO toca la DB.

- [ ] **Step 1: Instalar dependencias opcionales**

Run: `venv/Scripts/python.exe -m pip install -r requirements-oss.txt`
Expected: instala sentence-transformers y numpy (torch CPU incluido; puede tardar).

- [ ] **Step 2: Escribir el script**

Crear `tests/eval_comparativo.py`:

```python
"""Eval comparativo offline: NO toca la DB (las dims difieren: bge-m3 1024 vs e5-base 768).

Uso:
    venv/Scripts/python.exe tests/eval_comparativo.py --embeddings           # nvidia vs st, hit@k + gate
    venv/Scripts/python.exe tests/eval_comparativo.py --sintesis 5          # NIM vs Ollama lado a lado

Requiere: pip install -r requirements-oss.txt; para --sintesis, Ollama corriendo con el modelo bajado.
"""
import argparse
import sys

import numpy as np
from dotenv import load_dotenv

sys.path.insert(0, ".")

from api.confidence import evaluar_confianza
from api.prompts import system_de_sintesis
from db.connection import get_connection
from db.queries import buscar_tramites
from ingest.mapper import texto_para_embedding
from providers import factory
from providers.openai_compat import OpenAICompatChatProvider
from providers.st_embeddings import SentenceTransformersEmbeddingProvider
from tests.eval_dataset import CASOS, normalizar


def cargar_corpus() -> list[dict]:
    with get_connection() as conn:
        filas = conn.execute(
            "SELECT id, nombre, descripcion, sinonimos FROM tramites WHERE activo AND embedding IS NOT NULL"
        ).fetchall()
    return [{"id": f[0], "nombre": f[1], "descripcion": f[2], "sinonimos": f[3]} for f in filas]


def evaluar_backend(nombre: str, embedder, corpus: list[dict]) -> None:
    textos = [texto_para_embedding(c) for c in corpus]
    print(f"[{nombre}] embebiendo corpus ({len(textos)} docs)...")
    docs = np.array(embedder.embed_documents(textos), dtype=np.float32)
    docs /= np.linalg.norm(docs, axis=1, keepdims=True)

    frases = [c["frase"] for c in CASOS]
    print(f"[{nombre}] embebiendo {len(frases)} consultas...")
    consultas = np.array([embedder.embed_query(f) for f in frases], dtype=np.float32)
    consultas /= np.linalg.norm(consultas, axis=1, keepdims=True)

    distancias = 1.0 - consultas @ docs.T  # distancia coseno
    hit1 = hit5 = directas = 0
    aclara_ok = ambiguas = gateadas = negativas = claro_mal = 0
    for i, caso in enumerate(CASOS):
        top5 = np.argsort(distancias[i])[:5]
        d5 = [float(distancias[i][j]) for j in top5]
        v = evaluar_confianza(d5)
        nombres = [normalizar(corpus[j]["nombre"]) for j in top5]
        if caso["clase"] == "directa":
            directas += 1
            esperado = normalizar(caso["esperado"])
            en1 = esperado in nombres[0]
            hit1 += en1
            hit5 += any(esperado in n for n in nombres)
            if v == "claro" and not en1:
                claro_mal += 1
        elif caso["clase"] == "ambigua":
            ambiguas += 1
            aclara_ok += v == "ambiguo"
        else:
            negativas += 1
            gateadas += v in ("lejano", "vacio")
            claro_mal += v == "claro"
    print(f"[{nombre}] hit@1 {hit1}/{directas}  hit@5 {hit5}/{directas}  "
          f"aclara {aclara_ok}/{ambiguas}  gateadas {gateadas}/{negativas}  claro_mal {claro_mal}")
    print(f"[{nombre}] nota: el gate usa umbrales calibrados para bge-m3; para {nombre} son solo indicativos\n")


def comparar_sintesis(n: int) -> None:
    import os

    os.makedirs("docs/evals", exist_ok=True)
    nim = factory.chat_potente()  # PROVIDER default (nvidia)
    ollama = OpenAICompatChatProvider(
        model=os.environ.get("MODELO_POTENTE_OLLAMA", "llama3.1:8b"),
        base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        api_key="ollama",
    )
    emb = factory.embedder()
    directas = [c for c in CASOS if c["clase"] == "directa"][:n]
    lineas = ["# Síntesis comparada NIM vs Ollama\n"]
    with get_connection() as conn:
        for caso in directas:
            hits = buscar_tramites(conn, emb.embed_query(caso["frase"]), limit=1)
            if not hits:
                continue
            system = system_de_sintesis(hits[0])
            mensajes = [{"role": "user", "content": caso["frase"]}]
            lineas.append(f"\n## {caso['frase']}\n")
            lineas.append(f"**Trámite:** {hits[0]['nombre']}\n")
            lineas.append("### NIM\n\n" + nim.complete(system=system, messages=mensajes, max_tokens=800) + "\n")
            lineas.append("### Ollama\n\n" + ollama.complete(system=system, messages=mensajes, max_tokens=800) + "\n")
            print(f"ok: {caso['frase']}")
    with open("docs/evals/sintesis-comparada.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lineas))
    print("escrito docs/evals/sintesis-comparada.md")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--embeddings", action="store_true")
    parser.add_argument("--sintesis", type=int, default=0)
    args = parser.parse_args()

    if args.embeddings:
        corpus = cargar_corpus()
        evaluar_backend("nvidia/bge-m3", factory.embedder(), corpus)
        evaluar_backend("st/multilingual-e5-base", SentenceTransformersEmbeddingProvider(), corpus)
    if args.sintesis:
        comparar_sintesis(args.sintesis)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Correr el eval de embeddings**

Run: `venv/Scripts/python.exe tests/eval_comparativo.py --embeddings`
Expected: dos bloques de métricas comparables. (bge-m3 vía API con rate limit: el corpus completo tarda ~10-15 min; e5 local depende del CPU.) Anotar el resultado en `DECISIONS.md` (sección nueva "## Eval comparativo de embeddings") — la decisión de migrar o no queda para después del MVP.

- [ ] **Step 4: Correr la comparación de síntesis (si hay Ollama)**

Run: `ollama pull llama3.1:8b` (si no está) y `venv/Scripts/python.exe tests/eval_comparativo.py --sintesis 5`
Expected: `docs/evals/sintesis-comparada.md` con 5 pares NIM/Ollama. Si no hay Ollama instalado en esta máquina, anotarlo en `DECISIONS.md` y dejar el script listo (no bloquea el MVP: es opcionalidad).

- [ ] **Step 5: Correr la suite y commitear**

Run: `venv/Scripts/python.exe -m pytest tests/ -q` → Expected: PASS.

```bash
git add tests/eval_comparativo.py DECISIONS.md
git add docs/evals/  # solo si la comparación de síntesis corrió
git commit -m "feat: eval comparativo offline de embeddings y sintesis (Task 17 MVP)"
```

---

## Cierre

### Task 18: README, guía de sesión con usuarios y verificación final

**Files:**
- Modify: `README.md`, `ROADMAP.md`
- Create: `docs/guia-sesion-usuarios.md`

**Interfaces:**
- Consumes: todo lo anterior.
- Produces: documentación operativa + verificación de los criterios de éxito del spec.

- [ ] **Step 1: Actualizar README**

Agregar al README (después de la sección de uso existente) las secciones:

```markdown
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
```

- [ ] **Step 2: Guía de sesión con usuarios**

Crear `docs/guia-sesion-usuarios.md`:

```markdown
# Guía: sesión de prueba con usuarios reales (cierre del MVP)

Objetivo: 3-5 personas, ~15 min cada una, frases reales para re-calibrar.

## Protocolo

1. Levantar el server local y verificar `/health`.
2. Consigna a la persona (no mostrar ejemplos antes — sesgan el lenguaje):
   "Preguntale al asistente sobre 3 trámites que hayas hecho o tengas pendientes,
   como le escribirías a un conocido por WhatsApp."
3. No intervenir salvo bloqueo total. Anotar: reacciones, respuestas confusas,
   aclaraciones que molestaron.
4. Al final preguntar: ¿la respuesta te habría servido en la vida real? ¿qué faltó?

## Después de la sesión

1. Exportar las frases: `SELECT ts, mensaje, veredicto, respuesta_tipo FROM consultas_log ORDER BY ts DESC;`
2. Etiquetar cada frase (directa/ambigua/no_satisfacible) y agregarlas a `tests/eval_dataset.py`.
3. Re-correr `tests/verificar_eval.py` y `tests/calibrar_gate.py`; ajustar umbrales si cambia la recomendación.
4. Verificar los criterios de éxito del spec (abajo) y anotar resultados en DECISIONS.md.

## Criterios de éxito (spec 2026-07-14)

- [ ] hit@5 >= 90% en directas
- [ ] cero "claro" incorrecto
- [ ] <= 25% de directas caen en aclaración innecesaria
- [ ] 100% de no satisfacibles sin inventar datos
```

- [ ] **Step 3: Actualizar ROADMAP**

En `ROADMAP.md`, sección "Fase 2 — MVP completo": marcar los 6 ítems de backend como hechos con referencia al spec/plan (ej. "✔ implementado — ver docs/superpowers/specs/2026-07-14-mvp-backend-design.md"), dejando explícito que "Frontend en TypeScript" sigue pendiente y que la migración de embeddings a open-source quedó evaluada pero no ejecutada (resultado en DECISIONS.md).

- [ ] **Step 4: Verificación final**

Run: `venv/Scripts/python.exe -m pytest tests/ -q` → Expected: todo verde.
Run: `venv/Scripts/python.exe tests/eval_retrieval.py` → Expected: hit@5 de directas ≥ 90%; anotar el número real.
Run: `venv/Scripts/python.exe tests/calibrar_gate.py` → Expected: la fila de umbrales actuales cumple: claro_incorrecto = 0, negativas gateadas 100%, one-shot ≥ 75% de directas. Si algo no se cumple, es el estado real — anotarlo en DECISIONS.md como pendiente de la sesión con usuarios, no maquillarlo.
Prueba E2E manual: una consulta directa ("cobrar la renta dignidad"), una ambigua ("necesito un certificado" → debe preguntar UNA vez y responder a la siguiente), una no satisfacible ("sacar pasaporte" → fetch en vivo o no-encontrado honesto).

- [ ] **Step 5: Commit final**

```bash
git add README.md ROADMAP.md docs/guia-sesion-usuarios.md
git commit -m "docs: README operativo, guia de sesion con usuarios y cierre del MVP backend (Task 18)"
```

La sesión con usuarios reales (protocolo de la guía) queda como actividad humana post-plan: el usuario la agenda, y las frases recolectadas retroalimentan eval y umbrales.
