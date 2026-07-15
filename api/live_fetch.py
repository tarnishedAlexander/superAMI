"""Fetch en vivo (paso online 4 del spec): cuando el retrieval no encuentra nada
usable, se trae la página oficial vía `enlaces` del registro más cercano y se
extrae con el mismo modelo potente y esquema que el paso offline. Fail-soft total."""
import logging

import httpx
from bs4 import BeautifulSoup

from db.connection import get_connection
from db.queries import guardar_fetch_cache, leer_fetch_cache
from ingest.costo_llm import extraer_costo
from providers.base import ChatProvider

logger = logging.getLogger(__name__)

TIMEOUT_SEGUNDOS = 10.0
MAX_BYTES = 2_000_000
MAX_CHARS_TEXTO = 8000
MIN_CHARS_TEXTO = 200
TTL_DIAS = 7


def primera_url(enlaces) -> str | None:
    for enlace in enlaces or []:
        if isinstance(enlace, str) and enlace.startswith("http"):
            return enlace
        if isinstance(enlace, dict):
            for clave in ("url", "enlace", "href", "link"):
                valor = enlace.get(clave)
                if isinstance(valor, str) and valor.startswith("http"):
                    return valor
    return None


def extraer_texto(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    texto = " ".join(soup.get_text(" ").split())
    return texto[:MAX_CHARS_TEXTO]


def fetch_pagina(url: str) -> str | None:
    """Fail-soft: None ante cualquier problema de red o tamaño."""
    try:
        with httpx.Client(timeout=TIMEOUT_SEGUNDOS, follow_redirects=True) as client:
            respuesta = client.get(url)
            respuesta.raise_for_status()
            if len(respuesta.content) > MAX_BYTES:
                return None
            return respuesta.text
    except Exception:
        logger.warning("fetch en vivo falló para %s", url, exc_info=True)
        return None


def buscar_en_vivo(chat_potente: ChatProvider, candidatos: list[dict]) -> dict | None:
    """Intenta responder desde la página externa del candidato más cercano con enlaces.

    Devuelve {"url", "texto", "costo"} o None. Fail-soft total: nunca lanza.
    """
    try:
        url = None
        for candidato in candidatos or []:
            url = primera_url(candidato.get("enlaces"))
            if url:
                break
        if not url:
            return None

        with get_connection() as conn:
            cacheado = leer_fetch_cache(conn, url, ttl_dias=TTL_DIAS)
        if cacheado is not None:
            return cacheado

        html = fetch_pagina(url)
        if not html:
            return None
        texto = extraer_texto(html)
        if len(texto) < MIN_CHARS_TEXTO:
            return None

        costo = extraer_costo(chat_potente, texto, None)
        datos = {"url": url, "texto": texto, "costo": costo}
        with get_connection() as conn:
            guardar_fetch_cache(conn, url, datos)
            conn.commit()
        return datos
    except Exception:
        logger.warning("buscar_en_vivo falló", exc_info=True)
        return None
