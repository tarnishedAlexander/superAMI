"""Construcción compartida de las dependencias del pipeline (DB, providers, store).

Se separó de api/main.py para que api/whatsapp.py pueda importar get_deps sin
crear un import circular (main.py incluye el router de whatsapp, y whatsapp.py
necesita las mismas deps que usa /chat).
"""

from api.conversations_pg import PostgresConversationStore
from api.pipeline import Deps
from db.connection import get_connection
from db.queries import listar_categorias_dominio, listar_eventos
from providers import factory

_deps: Deps | None = None


def get_deps() -> Deps:
    global _deps
    if _deps is None:
        with get_connection() as conn:
            catalogos = {
                # solo categorías con trámites en el dominio MVP: ninguna categoría
                # inferible puede producir 0 hits por construcción
                "categorias": [c["slug"] for c in listar_categorias_dominio(conn)],
                "eventos": listar_eventos(conn),
            }
        store = PostgresConversationStore()
        store.limpiar_viejas(horas=24)
        _deps = Deps(
            chat_economico=factory.chat_economico(),
            chat_potente=factory.chat_potente(),
            embedder=factory.embedder(),
            store=store,
            catalogos=catalogos,
        )
    return _deps
