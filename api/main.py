import json
import logging

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.conversations_pg import PostgresConversationStore
from api.pipeline import Deps, procesar_mensaje
from db.connection import get_connection
from db.queries import listar_categorias_dominio, listar_eventos
from providers import factory

load_dotenv()
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="AMI — Asistente de Trámites Bolivia")

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


class ChatRequest(BaseModel):
    mensaje: str
    conversation_id: str | None = None


def _sse(evento: str, data: dict) -> str:
    return f"event: {evento}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/chat")
def chat(request: ChatRequest, deps: Deps = Depends(get_deps)):
    cid = deps.store.get_or_create(request.conversation_id)

    def generar():
        for evento, data in procesar_mensaje(deps, cid, request.mensaje):
            yield _sse(evento, {"conversation_id": cid, **data})

    return StreamingResponse(generar(), media_type="text/event-stream")
