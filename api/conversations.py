import uuid


class ConversationStore:
    """Historial en memoria por conversation_id. Se pierde al reiniciar (aceptable para el demo)."""

    def __init__(self):
        self._historiales: dict[str, list[dict]] = {}

    def get_or_create(self, conversation_id: str | None) -> str:
        cid = conversation_id or str(uuid.uuid4())
        self._historiales.setdefault(cid, [])
        return cid

    def append(self, conversation_id: str, role: str, content: str) -> None:
        self._historiales[conversation_id].append({"role": role, "content": content})

    def mensajes(self, conversation_id: str) -> list[dict]:
        return list(self._historiales.get(conversation_id, []))

    def texto_de_consulta(self, conversation_id: str) -> str:
        return " ".join(
            m["content"] for m in self._historiales.get(conversation_id, []) if m["role"] == "user"
        )
