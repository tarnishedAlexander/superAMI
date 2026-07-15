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
