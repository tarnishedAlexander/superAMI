from fastapi.testclient import TestClient

import api.main as main
from api.conversations import ConversationStore
from api.pipeline import Deps


def _deps_fake():
    return Deps(chat_economico=None, chat_potente=None, embedder=None, store=ConversationStore(), catalogos={})


def _pipeline_fake(deps, cid, mensaje):
    yield ("answer", {"delta": f"eco: {mensaje}"})
    yield ("answer", {"done": True, "tramite_ids": [42]})


def test_chat_devuelve_sse_con_conversation_id(monkeypatch):
    deps = _deps_fake()
    main.app.dependency_overrides[main.get_deps] = lambda: deps
    monkeypatch.setattr(main, "procesar_mensaje", _pipeline_fake)
    client = TestClient(main.app)

    respuesta = client.post("/chat", json={"mensaje": "hola"})

    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/event-stream")
    cuerpo = respuesta.text
    assert "event: answer" in cuerpo
    assert '"delta": "eco: hola"' in cuerpo
    assert '"conversation_id"' in cuerpo
    assert '"tramite_ids": [42]' in cuerpo
    main.app.dependency_overrides.clear()


def test_health():
    client = TestClient(main.app)
    assert client.get("/health").json() == {"ok": True}
