import logging
from dataclasses import dataclass
from typing import Iterator

from api.confidence import evaluar_confianza
from api.conversations import ConversationStore
from api.live_fetch import buscar_en_vivo
from api.prompts import (
    SISTEMA_ACLARACION,
    SISTEMA_FILTROS,
    schema_filtros,
    system_de_sintesis,
    system_de_sintesis_en_vivo,
    usuario_aclaracion,
)
from db.connection import get_connection
from db.queries import buscar_entidad_slug, buscar_tramites, listar_relacionados, registrar_consulta
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


def _registrar(conversation_id: str, mensaje: str, consulta: str, filtros: dict,
               hits: list[dict], veredicto: str | None, respuesta_tipo: str) -> None:
    """Fail-soft: el log nunca rompe la respuesta."""
    try:
        with get_connection() as conn:
            registrar_consulta(conn, {
                "conversation_id": conversation_id,
                "mensaje": mensaje,
                "consulta_acumulada": consulta,
                "filtros": filtros or None,
                "top_ids": [h["id"] for h in hits],
                "top_distancias": [round(h["distancia"], 4) for h in hits],
                "veredicto": veredicto,
                "respuesta_tipo": respuesta_tipo,
            })
            conn.commit()
    except Exception:
        logger.warning("no se pudo registrar la consulta en consultas_log", exc_info=True)


def procesar_mensaje(deps: Deps, conversation_id: str, mensaje: str) -> Iterator[tuple[str, dict]]:
    consulta = mensaje
    filtros: dict = {}
    hits: list[dict] = []
    veredicto: str | None = None
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
            datos_vivos = buscar_en_vivo(deps.chat_potente, hits[:3])
            if datos_vivos is None:
                deps.store.append(conversation_id, "assistant", MENSAJE_NO_ENCONTRADO, tipo="not_found")
                _registrar(conversation_id, mensaje, consulta, filtros, hits, veredicto, "not_found")
                yield ("answer", {"delta": MENSAJE_NO_ENCONTRADO})
                yield ("answer", {"done": True, "tramite_ids": []})
                return
            partes = []
            for delta in deps.chat_potente.stream(
                system=system_de_sintesis_en_vivo(datos_vivos),
                messages=deps.store.mensajes(conversation_id),
                max_tokens=4096,
            ):
                partes.append(delta)
                yield ("answer", {"delta": delta})
            deps.store.append(conversation_id, "assistant", "".join(partes), tipo="answer")
            _registrar(conversation_id, mensaje, consulta, filtros, hits, veredicto, "answer")
            yield ("answer", {"done": True, "tramite_ids": []})
            return

        if veredicto == "ambiguo" and deps.store.contar_aclaraciones(conversation_id) == 0:
            pregunta = formular_aclaracion(deps, consulta, hits[:3])
            deps.store.append(conversation_id, "assistant", pregunta, tipo="clarification")
            _registrar(conversation_id, mensaje, consulta, filtros, hits, veredicto, "clarification")
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
        relacionados: list[dict] = []
        try:
            with get_connection() as conn:
                relacionados = listar_relacionados(conn, top["id"])
        except Exception:
            logger.warning("no se pudieron leer relacionados de %s", top["id"], exc_info=True)
        partes: list[str] = []
        for delta in deps.chat_potente.stream(
            system=system_de_sintesis(top, alternativas=alternativas, relacionados=relacionados or None),
            messages=deps.store.mensajes(conversation_id),
            max_tokens=4096,
        ):
            partes.append(delta)
            yield ("answer", {"delta": delta})
        deps.store.append(conversation_id, "assistant", "".join(partes), tipo="answer")
        _registrar(conversation_id, mensaje, consulta, filtros, hits, veredicto, "answer")
        yield ("answer", {"done": True, "tramite_ids": [top["id"]]})
    except Exception:
        logger.exception("error procesando mensaje en conversación %s", conversation_id)
        _registrar(conversation_id, mensaje, consulta, filtros, hits, veredicto, "error")
        yield ("error", {"message": MENSAJE_ERROR})
