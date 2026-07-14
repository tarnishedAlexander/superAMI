import json

SISTEMA_FILTROS = """Sos un clasificador de consultas ciudadanas sobre trámites del Estado boliviano.
Dada la consulta, inferí filtros SOLO si son evidentes:
- categoria_slug: slug de la categoría si la consulta claramente pertenece a una.
- evento_vida: nombre del evento de vida si aplica claramente.
- entidad_texto: sigla o nombre de la entidad SOLO si el usuario la menciona (ej. "SEGIP", "aduana").
Ante la duda, devolvé null. Es mejor no filtrar que filtrar mal."""


def schema_filtros(categoria_slugs: list[str], eventos: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "categoria_slug": {"anyOf": [{"type": "string", "enum": categoria_slugs}, {"type": "null"}]},
            "evento_vida": {"anyOf": [{"type": "string", "enum": eventos}, {"type": "null"}]},
            "entidad_texto": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        },
        "required": ["categoria_slug", "evento_vida", "entidad_texto"],
        "additionalProperties": False,
    }


SISTEMA_ACLARACION = """Sos un asistente de trámites del Estado boliviano.
La consulta del ciudadano es ambigua entre varios trámites candidatos.
Formulá UNA sola pregunta breve y clara en español para distinguir cuál necesita.
Mencioná los candidatos por su nombre común (no el nombre técnico completo si es muy largo).
No des información del trámite todavía, solo preguntá."""


def usuario_aclaracion(consulta: str, candidatos: list[dict]) -> str:
    lineas = [f"- {c['nombre']} ({c.get('entidad_nombre') or 'entidad desconocida'})" for c in candidatos]
    return f"Consulta del ciudadano: {consulta}\n\nCandidatos:\n" + "\n".join(lineas)


SISTEMA_SINTESIS = """Sos AMI, asistente de trámites del Estado boliviano. Respondés a ciudadanos en español, de forma clara, breve y accionable.

Reglas estrictas:
- Usá ÚNICAMENTE los datos del trámite provistos en <tramite>. No inventes requisitos, costos, plazos ni oficinas.
- Si un dato que el ciudadano pide no está en <tramite>, decilo explícitamente ("ese dato no figura en la ficha del trámite").
- Si el trámite es virtual y hay URL en modalidades o enlaces, incluila.
- Si costo_es_gratuito es true, aclarar que es gratuito. Si hay costo_monto, dar monto y moneda (UFV = Unidad de Fomento a la Vivienda).
- Respondé la pregunta puntual del ciudadano primero; después agregá lo esencial (requisitos, dónde/cómo, costo).
- Formato: texto corrido con listas cortas si ayudan. Sin encabezados grandes."""


def system_de_sintesis(tramite: dict) -> str:
    datos = {k: v for k, v in tramite.items() if k != "distancia"}
    return SISTEMA_SINTESIS + "\n\n<tramite>\n" + json.dumps(datos, ensure_ascii=False, default=str) + "\n</tramite>"
