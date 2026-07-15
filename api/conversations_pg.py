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
