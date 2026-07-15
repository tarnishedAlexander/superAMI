import logging
from dataclasses import dataclass
from typing import Iterator

from api.confidence import evaluar_confianza
from api.conversations import ConversationStore
from api.prompts import SISTEMA_ACLARACION, SISTEMA_FILTROS, schema_filtros, system_de_sintesis, usuario_aclaracion
from db.connection import get_connection
from db.queries import buscar_entidad_slug, buscar_tramites
from providers.base import ChatProvider, EmbeddingProvider

logger = logging.getLogger(__name__)

MENSAJE_NO_ENCONTRADO = (
    "No encontré un trámite que coincida con tu consulta. ¿Podés reformularla con otras palabras?"
)
MENSAJE_ERROR = "Hubo un problema procesando tu consulta. Intentá de nuevo en un momento."


@dataclass
class Deps:
    chat_economico: ChatProvider
    chat_potente: ChatProvider
    embedder: EmbeddingProvider
    store: ConversationStore
    catalogos: dict


def fetch_live_fallback(consulta: str) -> None:
    # TODO fase "MVP completo" (ver ROADMAP.md): fetch de la página externa vía
    # el campo `enlaces` del registro más cercano + extracción con el modelo potente.
    return None


def inferir_filtros(deps: Deps, consulta: str) -> dict:
    """Infiere filtros con el modelo económico. Fail-open: ante cualquier problema devuelve {}."""
    categorias = deps.catalogos.get("categorias") or []
    eventos = deps.catalogos.get("eventos") or []
    if not categorias or not eventos:
        return {}
    try:
        datos = deps.chat_economico.complete_json(
            system=SISTEMA_FILTROS,
            messages=[{"role": "user", "content": consulta}],
            schema=schema_filtros(categorias, eventos),
            max_tokens=300,
        )
    except Exception:
        logger.warning("inferencia de filtros falló, sigo sin filtros", exc_info=True)
        return {}
    if not datos:
        return {}
    filtros: dict = {}
    if datos.get("categoria_slug"):
        filtros["categoria_slug"] = datos["categoria_slug"]
    if datos.get("evento_vida"):
        filtros["evento_nombre"] = datos["evento_vida"]
    if datos.get("entidad_texto"):
        try:
            with get_connection() as conn:
                slug = buscar_entidad_slug(conn, datos["entidad_texto"])
            if slug:
                filtros["entidad_slug"] = slug
        except Exception:
            logger.warning("búsqueda de entidad falló, sigo sin ese filtro", exc_info=True)
    return filtros


def formular_aclaracion(deps: Deps, consulta: str, candidatos: list[dict]) -> str:
    return deps.chat_economico.complete(
        system=SISTEMA_ACLARACION,
        messages=[{"role": "user", "content": usuario_aclaracion(consulta, candidatos)}],
        max_tokens=300,
    )


def procesar_mensaje(deps: Deps, conversation_id: str, mensaje: str) -> Iterator[tuple[str, dict]]:
    try:
        deps.store.append(conversation_id, "user", mensaje)
        consulta = deps.store.texto_de_consulta(conversation_id)

        filtros = inferir_filtros(deps, consulta)
        embedding = deps.embedder.embed_query(consulta)

        with get_connection() as conn:
            hits = buscar_tramites(conn, embedding, **filtros)
            if not hits and filtros:
                # fail-open: los filtros pueden haber sido mal inferidos
                hits = buscar_tramites(conn, embedding)

        veredicto = evaluar_confianza([h["distancia"] for h in hits])

        if veredicto in ("vacio", "lejano"):
            fetch_live_fallback(consulta)
            deps.store.append(conversation_id, "assistant", MENSAJE_NO_ENCONTRADO, tipo="not_found")
            yield ("answer", {"delta": MENSAJE_NO_ENCONTRADO})
            yield ("answer", {"done": True, "tramite_ids": []})
            return

        if veredicto == "ambiguo" and deps.store.contar_aclaraciones(conversation_id) == 0:
            pregunta = formular_aclaracion(deps, consulta, hits[:3])
            deps.store.append(conversation_id, "assistant", pregunta, tipo="clarification")
            yield ("clarification", {"text": pregunta})
            return

        # claro, o ambiguo con el tope de 1 aclaración alcanzado: responder igual, con transparencia
        forzado = veredicto == "ambiguo"
        top = hits[0]
        alternativas = (
            [{"nombre": h["nombre"], "entidad_nombre": h.get("entidad_nombre")} for h in hits[1:4]]
            if forzado
            else None
        )
        partes: list[str] = []
        for delta in deps.chat_potente.stream(
            system=system_de_sintesis(top, alternativas=alternativas),
            messages=deps.store.mensajes(conversation_id),
            max_tokens=4096,
        ):
            partes.append(delta)
            yield ("answer", {"delta": delta})
        deps.store.append(conversation_id, "assistant", "".join(partes), tipo="answer")
        yield ("answer", {"done": True, "tramite_ids": [top["id"]]})
    except Exception:
        logger.exception("error procesando mensaje en conversación %s", conversation_id)
        yield ("error", {"message": MENSAJE_ERROR})
