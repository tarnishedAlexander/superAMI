from contextlib import contextmanager

import api.pipeline as pipeline
from api.conversations import ConversationStore
from api.pipeline import Deps, procesar_mensaje


class FakeChat:
    def __init__(self, json_result=None, texto="¿Te referís a A o B?", deltas=("Hola", " mundo")):
        self._json = json_result
        self._texto = texto
        self._deltas = deltas

    def complete(self, *, system, messages, max_tokens=1024):
        return self._texto

    def complete_json(self, *, system, messages, schema, max_tokens=1024):
        return self._json

    def stream(self, *, system, messages, max_tokens=4096):
        yield from self._deltas


class FakeEmbedder:
    def embed_documents(self, texts):
        return [[0.0] * 1024 for _ in texts]

    def embed_query(self, text):
        return [0.0] * 1024


def _deps(**kwargs):
    return Deps(
        chat_economico=kwargs.get("economico", FakeChat(json_result=None)),
        chat_potente=kwargs.get("potente", FakeChat()),
        embedder=FakeEmbedder(),
        store=ConversationStore(),
        catalogos={"categorias": ["empresas"], "eventos": ["Empleo"]},
    )


def _hit(id_, nombre, distancia):
    return {"id": id_, "nombre": nombre, "descripcion": "d", "resultado": "r", "marco_legal": None,
            "canal": "virtual", "digitalizado": True, "costo_monto": 10.0, "costo_moneda": "Bs",
            "costo_concepto": None, "costo_es_gratuito": False, "requisitos": [], "documentos": [],
            "ubicaciones": [], "modalidades": [], "enlaces": [], "entidad_nombre": "SEGIP",
            "entidad_sitio_web": None, "distancia": distancia}


class _FakeConn:
    def commit(self):
        pass


@contextmanager
def _conn_fake():
    yield _FakeConn()


def _preparar(monkeypatch, hits):
    monkeypatch.setattr(pipeline, "get_connection", _conn_fake)
    monkeypatch.setattr(pipeline, "buscar_tramites", lambda conn, emb, **kw: hits)


def test_caso_claro_streamea_respuesta(monkeypatch):
    _preparar(monkeypatch, [_hit(1, "CEDULA", 0.2), _hit(2, "PASAPORTE", 0.4)])
    deps = _deps()
    cid = deps.store.get_or_create(None)
    eventos = list(procesar_mensaje(deps, cid, "quiero sacar mi carnet"))
    assert ("answer", {"delta": "Hola"}) in eventos
    assert eventos[-1] == ("answer", {"done": True, "tramite_ids": [1]})
    historial = deps.store.mensajes(cid)
    assert historial[-1] == {"role": "assistant", "content": "Hola mundo"}


def test_caso_ambiguo_pregunta_aclaracion(monkeypatch):
    _preparar(monkeypatch, [_hit(1, "A", 0.300), _hit(2, "B", 0.301)])
    deps = _deps()
    cid = deps.store.get_or_create(None)
    eventos = list(procesar_mensaje(deps, cid, "papel"))
    assert eventos == [("clarification", {"text": "¿Te referís a A o B?"})]


def test_sin_resultados_responde_no_encontrado(monkeypatch):
    _preparar(monkeypatch, [])
    deps = _deps()
    cid = deps.store.get_or_create(None)
    eventos = list(procesar_mensaje(deps, cid, "xyzabc"))
    assert eventos[0][0] == "answer"
    assert "No encontré" in eventos[0][1]["delta"]


def test_error_interno_emite_evento_error(monkeypatch):
    monkeypatch.setattr(pipeline, "get_connection", _conn_fake)

    def explota(conn, emb, **kw):
        raise RuntimeError("db caída")

    monkeypatch.setattr(pipeline, "buscar_tramites", explota)
    deps = _deps()
    cid = deps.store.get_or_create(None)
    eventos = list(procesar_mensaje(deps, cid, "hola"))
    assert eventos[-1][0] == "error"


def test_aclaracion_concatena_consulta(monkeypatch):
    _preparar(monkeypatch, [_hit(1, "A", 0.300), _hit(2, "B", 0.301)])
    deps = _deps()
    cid = deps.store.get_or_create(None)
    list(procesar_mensaje(deps, cid, "papel del carro"))
    deps.store.append(cid, "user", "el de propiedad")
    assert deps.store.texto_de_consulta(cid) == "papel del carro el de propiedad"


def test_lejano_responde_no_encontrado(monkeypatch):
    # d1 > 0.52 (umbral default): el gate dice "lejano", no se pregunta ni se sintetiza
    _preparar(monkeypatch, [_hit(1, "ALGO LEJANO", 0.80), _hit(2, "OTRO", 0.99)])
    deps = _deps()
    cid = deps.store.get_or_create(None)
    eventos = list(procesar_mensaje(deps, cid, "algo rarísimo"))
    assert eventos[0][0] == "answer"
    assert "No encontré" in eventos[0][1]["delta"]


def test_segunda_ambigua_fuerza_respuesta(monkeypatch):
    # primera pasada ambigua -> aclaración; segunda pasada ambigua -> respuesta forzada
    _preparar(monkeypatch, [_hit(1, "A", 0.300), _hit(2, "B", 0.301)])
    deps = _deps()
    cid = deps.store.get_or_create(None)
    eventos1 = list(procesar_mensaje(deps, cid, "papel"))
    assert eventos1[0][0] == "clarification"
    eventos2 = list(procesar_mensaje(deps, cid, "no sé, el común"))
    assert eventos2[0][0] == "answer"
    assert eventos2[-1] == ("answer", {"done": True, "tramite_ids": [1]})


def test_respuesta_forzada_pasa_alternativas_al_prompt(monkeypatch):
    _preparar(monkeypatch, [_hit(1, "A", 0.300), _hit(2, "B", 0.301), _hit(3, "C", 0.330)])
    capturado = {}

    class ChatEspia(FakeChat):
        def stream(self, *, system, messages, max_tokens=4096):
            capturado["system"] = system
            yield "ok"

    deps = _deps(potente=ChatEspia())
    cid = deps.store.get_or_create(None)
    list(procesar_mensaje(deps, cid, "papel"))          # aclaración
    list(procesar_mensaje(deps, cid, "sigo sin saber"))  # forzada
    assert "B" in capturado["system"] and "C" in capturado["system"]


def test_registra_consulta_en_caso_claro(monkeypatch):
    _preparar(monkeypatch, [_hit(1, "CEDULA", 0.2), _hit(2, "PASAPORTE", 0.4)])
    registros = []
    monkeypatch.setattr(pipeline, "registrar_consulta", lambda conn, datos: registros.append(datos))
    deps = _deps()
    cid = deps.store.get_or_create(None)
    list(procesar_mensaje(deps, cid, "quiero sacar mi carnet"))
    assert len(registros) == 1
    assert registros[0]["veredicto"] == "claro"
    assert registros[0]["respuesta_tipo"] == "answer"
    assert registros[0]["top_ids"] == [1, 2]


def test_log_roto_no_rompe_la_respuesta(monkeypatch):
    _preparar(monkeypatch, [_hit(1, "CEDULA", 0.2), _hit(2, "PASAPORTE", 0.4)])

    def explota(conn, datos):
        raise RuntimeError("log caído")

    monkeypatch.setattr(pipeline, "registrar_consulta", explota)
    deps = _deps()
    cid = deps.store.get_or_create(None)
    eventos = list(procesar_mensaje(deps, cid, "quiero sacar mi carnet"))
    assert eventos[-1] == ("answer", {"done": True, "tramite_ids": [1]})
