from providers.base import ChatProvider

SCHEMA_COSTO = {
    "type": "object",
    "properties": {
        "monto": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "moneda": {"anyOf": [{"type": "string", "enum": ["Bs", "USD", "UFV"]}, {"type": "null"}]},
        "concepto": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": ["monto", "moneda", "concepto"],
    "additionalProperties": False,
}

SISTEMA_COSTO = """Sos un extractor de datos de trámites del Estado boliviano.
Dado el texto de un trámite, extraé el costo monetario para el ciudadano si está mencionado explícitamente.
Reglas:
- monto: número exacto mencionado en el texto, o null si no se menciona ningún monto.
- moneda: "Bs" (bolivianos), "USD" (dólares) o "UFV". Si el texto no la aclara, asumí "Bs".
- concepto: descripción breve de qué se paga, o null.
- NO inventes montos. Si el texto no menciona un costo concreto, devolvé monto null."""


def extraer_costo(chat: ChatProvider, descripcion: str, resultado: str | None) -> dict | None:
    contenido = f"Descripción del trámite:\n{descripcion}\n\nResultado del trámite:\n{resultado or '(sin dato)'}"
    datos = chat.complete_json(
        system=SISTEMA_COSTO,
        messages=[{"role": "user", "content": contenido}],
        schema=SCHEMA_COSTO,
        max_tokens=300,
    )
    if not datos or datos.get("monto") is None:
        return None
    return datos
