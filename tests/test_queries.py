"""Tests de integración: requieren `docker compose up -d` corriendo."""
import pytest

from db.connection import get_connection
from db.queries import (
    buscar_entidad_slug,
    buscar_tramites,
    guardar_tramite_completo,
    listar_categorias,
    listar_eventos,
)

DIM = 1024


def _fila(id_, nombre, **overrides):
    fila = {
        "id": id_, "nombre": nombre, "slug": f"slug-{id_}", "sinonimos": ["prueba"],
        "descripcion": "desc", "resultado": "res", "marco_legal": None,
        "canal": "virtual", "digitalizado": True,
        "requisitos": [{"nombre": "CI", "comentario": None}], "documentos": [],
        "ubicaciones": [], "modalidades": [], "enlaces": [],
        "last_updated": None,
        "costo_monto": 10.0, "costo_moneda": "Bs", "costo_concepto": "pago",
        "costo_es_gratuito": False, "necesita_llm": False,
        "entidad": {"slug": "segip-test", "nombre": "SEGIP TEST", "sigla": "SGT", "sitio_web": None},
        "categorias": [{"slug": "cat-test", "nombre": "Cat Test"}],
        "eventos": ["Evento Test"],
    }
    fila.update(overrides)
    return fila


def _vec(valor_ultimo: float) -> list[float]:
    return [0.0] * (DIM - 1) + [valor_ultimo]


@pytest.fixture()
def conn():
    with get_connection() as c:
        yield c
        c.execute("DELETE FROM tramites WHERE id >= 900000")
        c.execute("DELETE FROM entidades WHERE slug = 'segip-test'")
        c.execute("DELETE FROM categorias WHERE slug = 'cat-test'")
        c.execute("DELETE FROM eventos_de_vida WHERE nombre = 'Evento Test'")
        c.commit()


def test_guardar_y_buscar(conn):
    guardar_tramite_completo(conn, _fila(900001, "TRAMITE CERCANO"), _vec(1.0))
    guardar_tramite_completo(conn, _fila(900002, "TRAMITE LEJANO"), _vec(-1.0))
    conn.commit()

    # limit alto: la DB puede tener datos reales cargados entre medio de las filas de prueba
    hits = [h for h in buscar_tramites(conn, _vec(1.0), limit=5000) if h["id"] >= 900000]
    assert [h["nombre"] for h in hits] == ["TRAMITE CERCANO", "TRAMITE LEJANO"]
    assert hits[0]["distancia"] < hits[1]["distancia"]
    assert hits[0]["entidad_nombre"] == "SEGIP TEST"


def test_upsert_idempotente(conn):
    guardar_tramite_completo(conn, _fila(900003, "NOMBRE VIEJO"), _vec(0.5))
    guardar_tramite_completo(conn, _fila(900003, "NOMBRE NUEVO"), _vec(0.5))
    conn.commit()
    nombre = conn.execute("SELECT nombre FROM tramites WHERE id = 900003").fetchone()[0]
    assert nombre == "NOMBRE NUEVO"


def test_filtros_y_catalogos(conn):
    guardar_tramite_completo(conn, _fila(900004, "CON CATEGORIA"), _vec(1.0))
    conn.commit()

    assert {"slug": "cat-test", "nombre": "Cat Test"} in listar_categorias(conn)
    assert "Evento Test" in listar_eventos(conn)
    assert buscar_entidad_slug(conn, "SGT") == "segip-test"
    assert buscar_entidad_slug(conn, "segip te") == "segip-test"
    assert buscar_entidad_slug(conn, "no-existe-xyz") is None

    con_filtro = buscar_tramites(conn, _vec(1.0), categoria_slug="cat-test")
    assert any(h["id"] == 900004 for h in con_filtro)
    sin_match = buscar_tramites(conn, _vec(1.0), categoria_slug="categoria-inexistente")
    assert sin_match == []


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
