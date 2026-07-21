"""Webhook de WhatsApp Cloud API.

Recibe los mensajes entrantes de WhatsApp, los pasa por el MISMO pipeline que
usa POST /chat (api/pipeline.py::procesar_mensaje), junta la respuesta en
streaming en un solo texto (WhatsApp no soporta streaming) y la manda de
vuelta llamando a la Graph API de Meta.

Importante: le respondemos 200 OK a Meta ANTES de terminar de procesar
(usando BackgroundTasks). Si tardamos varios segundos en responder al POST
del webhook (el pipeline llama a un LLM, puede tardar), Meta puede reintentar
la entrega y duplicar el mensaje. Procesar en background evita eso.

Variables de entorno requeridas (agregalas a .env, ver .env.example):
- WHATSAPP_TOKEN            access token (temporal de prueba, o permanente de
                             un system user para producción)
- WHATSAPP_PHONE_NUMBER_ID  id del número de teléfono, lo da Meta en
                             WhatsApp Manager / App Dashboard > WhatsApp > API Setup
- WHATSAPP_VERIFY_TOKEN     un string que vos inventás; se usa SOLO para el
                             handshake de verificación del webhook (paso GET)
- WHATSAPP_APP_SECRET       (opcional pero recomendado) para validar que los
                             POST entrantes vienen realmente de Meta y no de
                             un tercero que le pegue a tu URL
"""

import hashlib
import hmac
import json
import logging
import os

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response

from api.deps import get_deps
from api.pipeline import Deps, procesar_mensaje

logger = logging.getLogger(__name__)

router = APIRouter()

GRAPH_API_VERSION = "v23.0"  # versión actual de Graph API al momento de escribir esto (jul 2026)
LIMITE_TEXTO_WHATSAPP = 4096  # límite de WhatsApp por mensaje de texto


@router.get("/webhook/whatsapp")
def verificar_webhook(request: Request):
    """Meta llama este endpoint (GET) al configurar el webhook en el dashboard,
    para confirmar que la URL es tuya. Debe devolver el `hub.challenge` tal
    cual si el `hub.verify_token` coincide con el que configuraste."""
    params = request.query_params
    verify_token = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == verify_token:
        return Response(content=params.get("hub.challenge", ""), media_type="text/plain")
    return Response(status_code=403)


def _firma_valida(body: bytes, firma_header: str | None) -> bool:
    """Verifica el header X-Hub-Signature-256 que Meta manda en cada POST,
    calculado con el App Secret. Evita que cualquiera le pegue a tu webhook
    haciéndose pasar por Meta."""
    app_secret = os.environ.get("WHATSAPP_APP_SECRET")
    if not app_secret:
        logger.warning(
            "WHATSAPP_APP_SECRET no configurado: aceptando webhook sin validar firma "
            "(ok para el hackathon, configuralo antes de exponerlo públicamente en serio)"
        )
        return True
    if not firma_header:
        return False
    esperado = "sha256=" + hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, firma_header)


@router.post("/webhook/whatsapp")
async def recibir_mensaje(
    request: Request, background_tasks: BackgroundTasks, deps: Deps = Depends(get_deps)
):
    body = await request.body()
    if not _firma_valida(body, request.headers.get("x-hub-signature-256")):
        logger.warning("firma de webhook inválida, se descarta el POST")
        return Response(status_code=401)

    try:
        payload = json.loads(body)
        cambio = payload["entry"][0]["changes"][0]["value"]
    except (KeyError, IndexError, json.JSONDecodeError):
        logger.warning("payload de whatsapp con forma inesperada")
        return Response(status_code=200)

    mensajes = cambio.get("messages")
    if not mensajes:
        # eventos de status (entregado/leído) u otros — nada que responder
        return Response(status_code=200)

    mensaje_wa = mensajes[0]
    wa_id = mensaje_wa["from"]  # número del usuario, formato internacional sin "+"
    texto = mensaje_wa.get("text", {}).get("body")

    if not texto:
        background_tasks.add_task(
            _enviar_texto, wa_id, "Por ahora solo puedo leer mensajes de texto 🙂"
        )
        return Response(status_code=200)

    # procesar en background: responder rápido a Meta, evitar reintentos/duplicados
    background_tasks.add_task(_procesar_y_responder, deps, wa_id, texto)
    return Response(status_code=200)


def _procesar_y_responder(deps: Deps, wa_id: str, texto: str) -> None:
    try:
        # una conversación por número de WhatsApp; reusa el mismo store que /chat
        conversation_id = deps.store.get_or_create(f"whatsapp:{wa_id}")

        partes: list[str] = []
        respuesta_final: str | None = None
        for evento, data in procesar_mensaje(deps, conversation_id, texto):
            if evento == "answer" and "delta" in data:
                partes.append(data["delta"])
            elif evento == "clarification":
                respuesta_final = data["text"]
            elif evento == "error":
                respuesta_final = data["message"]

        if respuesta_final is None:
            respuesta_final = "".join(partes).strip()

        if respuesta_final:
            _enviar_texto(wa_id, respuesta_final)
    except Exception:
        logger.exception("error procesando mensaje de whatsapp de %s", wa_id)
        try:
            _enviar_texto(wa_id, "Hubo un problema procesando tu consulta. Intentá de nuevo en un momento.")
        except Exception:
            logger.exception("no se pudo avisar el error a %s", wa_id)


def _enviar_texto(to: str, texto: str) -> None:
    token = os.environ["WHATSAPP_TOKEN"]
    phone_number_id = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": texto[:LIMITE_TEXTO_WHATSAPP]},
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    with httpx.Client(timeout=15) as client:
        resp = client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            logger.error("error enviando whatsapp a %s: %s %s", to, resp.status_code, resp.text)
