import json

from psycopg.types.json import Json

_COLUMNAS_TRAMITE = [
    "id", "nombre", "slug", "sinonimos", "descripcion", "resultado", "marco_legal",
    "entidad_id", "costo_monto", "costo_moneda", "costo_concepto", "costo_es_gratuito",
    "requisitos", "documentos", "ubicaciones", "modalidades", "enlaces",
    "canal", "digitalizado", "embedding", "last_updated",
]


def _vector_literal(embedding: list[float] | None) -> str | None:
    if embedding is None:
        return None
    return "[" + ",".join(map(str, embedding)) + "]"


def upsert_entidad(conn, entidad: dict) -> int:
    fila = conn.execute(
        """
        INSERT INTO entidades (slug, nombre, sigla, sitio_web)
        VALUES (%(slug)s, %(nombre)s, %(sigla)s, %(sitio_web)s)
        ON CONFLICT (slug) DO UPDATE
          SET nombre = EXCLUDED.nombre, sigla = EXCLUDED.sigla, sitio_web = EXCLUDED.sitio_web
        RETURNING id
        """,
        entidad,
    ).fetchone()
    return fila[0]


def upsert_categoria(conn, categoria: dict) -> int:
    fila = conn.execute(
        """
        INSERT INTO categorias (slug, nombre) VALUES (%(slug)s, %(nombre)s)
        ON CONFLICT (slug) DO UPDATE SET nombre = EXCLUDED.nombre
        RETURNING id
        """,
        categoria,
    ).fetchone()
    return fila[0]


def upsert_evento(conn, nombre: str) -> int:
    fila = conn.execute(
        """
        INSERT INTO eventos_de_vida (nombre) VALUES (%(nombre)s)
        ON CONFLICT (nombre) DO UPDATE SET nombre = EXCLUDED.nombre
        RETURNING id
        """,
        {"nombre": nombre},
    ).fetchone()
    return fila[0]


def guardar_tramite_completo(conn, fila: dict, embedding: list[float] | None) -> None:
    entidad_id = upsert_entidad(conn, fila["entidad"])
    params = {
        **{k: fila.get(k) for k in _COLUMNAS_TRAMITE if k not in ("entidad_id", "embedding")},
        "entidad_id": entidad_id,
        "embedding": _vector_literal(embedding),
        "requisitos": Json(fila["requisitos"]),
        "documentos": Json(fila["documentos"]),
        "ubicaciones": Json(fila["ubicaciones"]),
        "modalidades": Json(fila["modalidades"]),
        "enlaces": Json(fila["enlaces"]),
    }
    columnas = ", ".join(_COLUMNAS_TRAMITE)
    placeholders = ", ".join(
        f"%({c})s::vector" if c == "embedding" else f"%({c})s" for c in _COLUMNAS_TRAMITE
    )
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in _COLUMNAS_TRAMITE if c != "id")
    conn.execute(
        f"INSERT INTO tramites ({columnas}) VALUES ({placeholders}) "
        f"ON CONFLICT (id) DO UPDATE SET {updates}",
        params,
    )

    conn.execute("DELETE FROM tramites_categorias WHERE tramite_id = %s", (fila["id"],))
    for categoria in fila["categorias"]:
        cat_id = upsert_categoria(conn, categoria)
        conn.execute(
            "INSERT INTO tramites_categorias (tramite_id, categoria_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (fila["id"], cat_id),
        )

    conn.execute("DELETE FROM tramites_eventos WHERE tramite_id = %s", (fila["id"],))
    for evento in fila["eventos"]:
        ev_id = upsert_evento(conn, evento)
        conn.execute(
            "INSERT INTO tramites_eventos (tramite_id, evento_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (fila["id"], ev_id),
        )


def listar_categorias(conn) -> list[dict]:
    filas = conn.execute("SELECT slug, nombre FROM categorias ORDER BY nombre").fetchall()
    return [{"slug": f[0], "nombre": f[1]} for f in filas]


def listar_eventos(conn) -> list[str]:
    return [f[0] for f in conn.execute("SELECT nombre FROM eventos_de_vida ORDER BY nombre").fetchall()]


def buscar_entidad_slug(conn, texto: str) -> str | None:
    fila = conn.execute(
        """
        SELECT slug FROM entidades
        WHERE sigla ILIKE %(t)s OR nombre ILIKE '%%' || %(t)s || '%%'
        ORDER BY (sigla ILIKE %(t)s) DESC
        LIMIT 1
        """,
        {"t": texto},
    ).fetchone()
    return fila[0] if fila else None


_SQL_BUSCAR = """
SELECT t.id, t.nombre, t.slug, t.descripcion, t.resultado, t.marco_legal,
       t.canal, t.digitalizado,
       t.costo_monto, t.costo_moneda, t.costo_concepto, t.costo_es_gratuito,
       t.requisitos, t.documentos, t.ubicaciones, t.modalidades, t.enlaces,
       e.nombre AS entidad_nombre, e.sitio_web AS entidad_sitio_web,
       t.embedding <=> %(emb)s::vector AS distancia
FROM tramites t
LEFT JOIN entidades e ON e.id = t.entidad_id
WHERE t.embedding IS NOT NULL
  AND (%(cat)s::text IS NULL OR EXISTS (
        SELECT 1 FROM tramites_categorias tc JOIN categorias c ON c.id = tc.categoria_id
        WHERE tc.tramite_id = t.id AND c.slug = %(cat)s))
  AND (%(ent)s::text IS NULL OR e.slug = %(ent)s)
  AND (%(ev)s::text IS NULL OR EXISTS (
        SELECT 1 FROM tramites_eventos te JOIN eventos_de_vida ev ON ev.id = te.evento_id
        WHERE te.tramite_id = t.id AND ev.nombre = %(ev)s))
ORDER BY t.embedding <=> %(emb)s::vector
LIMIT %(limit)s
"""


def buscar_tramites(
    conn,
    embedding: list[float],
    categoria_slug: str | None = None,
    entidad_slug: str | None = None,
    evento_nombre: str | None = None,
    limit: int = 5,
) -> list[dict]:
    cursor = conn.execute(
        _SQL_BUSCAR,
        {
            "emb": _vector_literal(embedding),
            "cat": categoria_slug,
            "ent": entidad_slug,
            "ev": evento_nombre,
            "limit": limit,
        },
    )
    nombres = [d.name for d in cursor.description]
    filas = []
    for tupla in cursor.fetchall():
        fila = dict(zip(nombres, tupla))
        fila["distancia"] = float(fila["distancia"])
        if fila["costo_monto"] is not None:
            fila["costo_monto"] = float(fila["costo_monto"])
        filas.append(fila)
    return filas


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
