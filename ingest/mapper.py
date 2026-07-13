import re
from datetime import date, datetime


def strip_html(texto: str | None) -> str | None:
    if texto is None:
        return None
    sin_tags = re.sub(r"<[^>]+>", " ", texto)
    limpio = re.sub(r"\s+", " ", sin_tags).strip()
    return limpio or None


def parsear_fecha(texto: str | None) -> date | None:
    if not texto:
        return None
    try:
        return datetime.strptime(texto, "%d/%m/%Y").date()
    except ValueError:
        return None


def mapear_canal(record: dict) -> str | None:
    presencial = bool(record.get("esPresencial"))
    virtual = bool(record.get("esVirtual"))
    if presencial and virtual:
        return "ambos"
    if virtual:
        return "virtual"
    if presencial:
        return "presencial"
    return None


def _publicos(record: dict):
    for modalidad in record.get("modalidades") or []:
        yield from modalidad.get("publico") or []


def mapear_costo(record: dict) -> dict:
    """Regla de costo (ver DECISIONS.md):
    - costos[] con monto parseable -> mapeo directo
    - ningún público con tieneCosto -> gratuito
    - tieneCosto=true sin costos -> fallback LLM (necesita_llm)
    """
    sin_costo = {"costo_monto": None, "costo_moneda": None, "costo_concepto": None}
    tiene_costo = False
    for publico in _publicos(record):
        tiene_costo = tiene_costo or bool(publico.get("tieneCosto"))
        for costo in publico.get("costos") or []:
            try:
                monto = float(costo.get("costo"))
            except (TypeError, ValueError):
                continue
            return {
                "costo_monto": monto,
                "costo_moneda": costo.get("moneda"),
                "costo_concepto": strip_html(costo.get("conceptoPago")),
                "costo_es_gratuito": False,
                "necesita_llm": False,
            }
    if not tiene_costo:
        return {**sin_costo, "costo_es_gratuito": True, "necesita_llm": False}
    return {**sin_costo, "costo_es_gratuito": False, "necesita_llm": True}


def aplanar_requisitos(record: dict) -> list[dict]:
    vistos: dict[str, dict] = {}
    for publico in _publicos(record):
        for requisito in publico.get("requisitos") or []:
            nombre = (requisito.get("nombre") or "").strip()
            if not nombre:
                continue
            comentario = strip_html(requisito.get("comentario"))
            if nombre not in vistos:
                vistos[nombre] = {"nombre": nombre, "comentario": comentario}
            elif vistos[nombre]["comentario"] is None and comentario:
                vistos[nombre]["comentario"] = comentario
    return list(vistos.values())


def _limpiar_modalidad(modalidad: dict) -> dict:
    publicos = [
        {**p, "procedimiento": strip_html(p.get("procedimiento"))}
        for p in modalidad.get("publico") or []
    ]
    return {**modalidad, "publico": publicos}


def mapear_tramite(record: dict) -> dict:
    entidad = record["entidad"]
    return {
        "id": int(record["id"]),
        "nombre": record["nombre"],
        "slug": record.get("slug"),
        "sinonimos": record.get("palabrasClave") or [],
        "descripcion": (record.get("descripcion") or "").strip(),
        "resultado": record.get("resultado"),
        "marco_legal": record.get("marcoLegal"),
        "canal": mapear_canal(record),
        "digitalizado": bool(record.get("esVirtual")),
        "requisitos": aplanar_requisitos(record),
        "documentos": record.get("documentos") or [],
        "ubicaciones": record.get("ubicaciones") or [],
        "modalidades": [_limpiar_modalidad(m) for m in record.get("modalidades") or []],
        "enlaces": record.get("enlaces") or [],
        "last_updated": parsear_fecha(record.get("fechaActualización")),
        "entidad": {
            "slug": entidad["slug"],
            "nombre": entidad["nombre"],
            "sigla": entidad.get("sigla"),
            "sitio_web": entidad.get("urlSitioWeb"),
        },
        "categorias": [{"slug": c["slug"], "nombre": c["nombre"]} for c in record.get("categorias") or []],
        "eventos": record.get("eventosVida") or [],
        **mapear_costo(record),
    }


def texto_para_embedding(fila: dict) -> str:
    partes = [fila["nombre"], fila.get("descripcion") or "", " ".join(fila.get("sinonimos") or [])]
    return "\n".join(p for p in partes if p).strip()
