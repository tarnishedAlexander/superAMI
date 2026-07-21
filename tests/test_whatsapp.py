import hashlib
import hmac
import json

from fastapi.testclient import TestClient

import api.main as main
import api.whatsapp as whatsapp
from api.conversations import ConversationStore
from api.pipeline import Deps


def _deps_fake():
    return Deps(chat_economico=None, chat_potente=None, embedder=None, store=ConversationStore(), catalogos={})


def _payload_mensaje(texto: str, wa_id: str = "59171234567") -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {"from": wa_id, "type": "text", "text": {"body": texto}}
                            ]
                        }
                    }
                ]
            }
        ]
    }


def _payload_status() -> dict:
    """Forma real de un evento de status (entregado/leído), sin 'messages'."""
    return {"entry": [{"changes": [{"value": {"statuses": [{"status": "delivered"}]}}]}]}


def test_verificar_webhook_ok(monkeypatch):
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "token-secreto")
    client = TestClient(main.app)

    respuesta = client.get(
        "/webhook/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "token-secreto", "hub.challenge": "1234"},
    )

    assert respuesta.status_code == 200
    assert respuesta.text == "1234"


def test_verificar_webhook_token_invalido(monkeypatch):
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "token-secreto")
    client = TestClient(main.app)

    respuesta = client.get(
        "/webhook/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "token-incorrecto", "hub.challenge": "1234"},
    )

    assert respuesta.status_code == 403


def test_post_sin_app_secret_no_valida_firma(monkeypatch):
    """Sin WHATSAPP_APP_SECRET configurado, se acepta el POST (modo dev/hackathon)."""
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    deps = _deps_fake()
    main.app.dependency_overrides[main.get_deps] = lambda: deps
    monkeypatch.setattr(whatsapp, "procesar_mensaje", lambda d, cid, m: iter([("answer", {"delta": "hola"})]))
    monkeypatch.setattr(whatsapp, "_enviar_texto", lambda to, texto: None)
    client = TestClient(main.app)

    respuesta = client.post("/webhook/whatsapp", json=_payload_mensaje("hola"))

    assert respuesta.status_code == 200
    main.app.dependency_overrides.clear()


def test_post_firma_invalida_se_rechaza(monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "shhh")
    deps = _deps_fake()
    main.app.dependency_overrides[main.get_deps] = lambda: deps
    client = TestClient(main.app)

    respuesta = client.post(
        "/webhook/whatsapp",
        json=_payload_mensaje("hola"),
        headers={"x-hub-signature-256": "sha256=firma_incorrecta"},
    )

    assert respuesta.status_code == 401
    main.app.dependency_overrides.clear()


def test_post_firma_valida_se_acepta_y_responde(monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "shhh")
    deps = _deps_fake()
    main.app.dependency_overrides[main.get_deps] = lambda: deps
    monkeypatch.setattr(
        whatsapp, "procesar_mensaje", lambda d, cid, m: iter([("answer", {"delta": f"eco: {m}"})])
    )
    enviados = []
    monkeypatch.setattr(whatsapp, "_enviar_texto", lambda to, texto: enviados.append((to, texto)))
    client = TestClient(main.app)
    cuerpo = json.dumps(_payload_mensaje("hola")).encode()
    firma = "sha256=" + hmac.new(b"shhh", cuerpo, hashlib.sha256).hexdigest()

    respuesta = client.post(
        "/webhook/whatsapp", content=cuerpo, headers={"x-hub-signature-256": firma, "content-type": "application/json"}
    )

    assert respuesta.status_code == 200
    assert enviados == [("59171234567", "eco: hola")]
    main.app.dependency_overrides.clear()


def test_post_evento_de_status_no_hace_nada(monkeypatch):
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    deps = _deps_fake()
    main.app.dependency_overrides[main.get_deps] = lambda: deps
    enviados = []
    monkeypatch.setattr(whatsapp, "_enviar_texto", lambda to, texto: enviados.append((to, texto)))
    client = TestClient(main.app)

    respuesta = client.post("/webhook/whatsapp", json=_payload_status())

    assert respuesta.status_code == 200
    assert enviados == []
    main.app.dependency_overrides.clear()
