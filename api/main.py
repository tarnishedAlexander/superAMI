import json
import logging

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api import whatsapp
from api.deps import get_deps
from api.pipeline import Deps, procesar_mensaje

load_dotenv()
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="AMI — Asistente de Trámites Bolivia")
app.include_router(whatsapp.router)


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
