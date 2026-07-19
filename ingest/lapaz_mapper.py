"""Extracción + mapeo de un post de trámite de lapaz.bo (GAMLP) al esquema `tramites`.

lapaz.bo es WordPress sin API pública: cada trámite es un post HTML bien
estructurado (secciones "En qué consiste", "Requisitos", costo, "Dónde se inicia",
"Pasos del ciudadano", tiempo). Este módulo define el prompt de extracción dirigida
(HTML/markdown -> JSON) y el mapeo de ese JSON a la fila que consume
`db.queries.guardar_tramite_completo`.
"""
from ingest.mapper import strip_html

# Entidad única para todos los trámites de La Paz. El nombre contiene "Municipal"
# a propósito: así caen dentro de la vista dominio_mvp (que marca por
# entidad ILIKE '%municipal%') sin tocar la vista.
ENTIDAD_GAMLP = {
    "slug": "gamlp-la-paz",
    "nombre": "Gobierno Autónomo Municipal de La Paz (GAMLP)",
    "sigla": "GAMLP",
    "sitio_web": "https://lapaz.bo",
}

SISTEMA_EXTRACCION = """Sos un extractor de datos de trámites del Gobierno Autónomo Municipal de La Paz.
Recibís el texto de una página oficial de un trámite y devolvés SOLO un objeto JSON
con estos campos (sin texto adicional):
- nombre: título del trámite.
- descripcion: qué es / en qué consiste, en 1-3 oraciones.
- requisitos: lista de objetos {nombre, comentario}. Juntá los de persona natural y
  jurídica; si un requisito aclara para quién aplica, ponelo en comentario.
- canal: "virtual" | "presencial" | "ambos" | null según cómo se hace el trámite.
- ubicaciones: lista de objetos {nombre, direccion} de oficinas/plataformas físicas.
- pasos: lista de strings con los pasos del ciudadano, en orden.
- tiempo: tiempo estimado de atención (ej. "24 a 48 hrs") o null.
- oficina: unidad/dirección municipal responsable, o null.
- resultado: qué obtiene el ciudadano al final, o null.
- costo_es_gratuito: true si el trámite es gratuito.
- costo_monto: número si tiene un costo explícito, si no null.
- costo_moneda: "Bs" u otra, o null.
- costo_concepto: descripción breve del costo, o null.
Si un dato no aparece, usá null (o lista vacía). No inventes."""


def schema_extraccion() -> dict:
    s = lambda: {"anyOf": [{"type": "string"}, {"type": "null"}]}  # noqa: E731
    return {
        "type": "object",
        "properties": {
            "nombre": {"type": "string"},
            "descripcion": {"type": "string"},
            "requisitos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"nombre": {"type": "string"}, "comentario": s()},
                    "required": ["nombre", "comentario"],
                    "additionalProperties": False,
                },
            },
            "canal": {"anyOf": [{"type": "string", "enum": ["virtual", "presencial", "ambos"]}, {"type": "null"}]},
            "ubicaciones": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"nombre": s(), "direccion": s()},
                    "required": ["nombre", "direccion"],
                    "additionalProperties": False,
                },
            },
            "pasos": {"type": "array", "items": {"type": "string"}},
            "tiempo": s(),
            "oficina": s(),
            "resultado": s(),
            "costo_es_gratuito": {"type": "boolean"},
            "costo_monto": {"anyOf": [{"type": "number"}, {"type": "null"}]},
            "costo_moneda": s(),
            "costo_concepto": s(),
        },
        "required": ["nombre", "descripcion", "requisitos", "canal", "ubicaciones",
                     "pasos", "tiempo", "oficina", "resultado",
                     "costo_es_gratuito", "costo_monto", "costo_moneda", "costo_concepto"],
        "additionalProperties": False,
    }


def _requisitos(datos: dict) -> list[dict]:
    salida = []
    for r in datos.get("requisitos") or []:
        if isinstance(r, dict):
            nombre = (r.get("nombre") or "").strip()
            comentario = strip_html(r.get("comentario"))
        else:
            nombre, comentario = str(r).strip(), None
        if nombre:
            salida.append({"nombre": nombre, "comentario": comentario})
    return salida


def _modalidades(datos: dict) -> list[dict]:
    pasos = [p for p in (datos.get("pasos") or []) if str(p).strip()]
    tiempo = strip_html(datos.get("tiempo"))
    oficina = strip_html(datos.get("oficina"))
    if not (pasos or tiempo or oficina):
        return []
    return [{"tipo": "gamlp", "pasos": pasos, "tiempo": tiempo, "oficina": oficina}]


def _costo(datos: dict) -> dict:
    try:
        monto = float(datos["costo_monto"]) if datos.get("costo_monto") is not None else None
    except (TypeError, ValueError):
        monto = None
    gratuito = bool(datos.get("costo_es_gratuito")) or (monto in (0, 0.0))
    return {
        "costo_monto": None if gratuito else monto,
        "costo_moneda": None if gratuito else datos.get("costo_moneda"),
        "costo_concepto": None if gratuito else strip_html(datos.get("costo_concepto")),
        "costo_es_gratuito": gratuito,
        "necesita_llm": False,
    }


def mapear_extraccion(datos: dict, tramite_id: int, slug: str, url: str, categoria: dict | None) -> dict:
    """JSON extraído -> fila para `guardar_tramite_completo` (fuente = lapaz_gamlp)."""
    nombre = (datos.get("nombre") or slug.replace("-", " ").title()).strip()
    return {
        "id": tramite_id,
        "nombre": nombre,
        "slug": slug,
        "sinonimos": [],
        "descripcion": strip_html(datos.get("descripcion")) or "",
        "resultado": strip_html(datos.get("resultado")),
        "marco_legal": None,
        "canal": datos.get("canal"),
        "digitalizado": datos.get("canal") in ("virtual", "ambos"),
        "requisitos": _requisitos(datos),
        "documentos": [],
        "ubicaciones": datos.get("ubicaciones") or [],
        "modalidades": _modalidades(datos),
        "enlaces": [{"url": url}],
        "last_updated": None,
        "entidad": dict(ENTIDAD_GAMLP),
        "categorias": [categoria] if categoria else [],
        "eventos": [],
        "fuente": "lapaz_gamlp",
        **_costo(datos),
    }
