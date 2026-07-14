from ingest.costo_llm import extraer_costo


class FakeChat:
    def __init__(self, respuesta):
        self._respuesta = respuesta
        self.ultimo_schema = None

    def complete_json(self, *, system, messages, schema, max_tokens=1024):
        self.ultimo_schema = schema
        return self._respuesta


def test_extraer_costo_encontrado():
    chat = FakeChat({"monto": 20.0, "moneda": "Bs", "concepto": "formulario"})
    datos = extraer_costo(chat, "El trámite cuesta 20 bolivianos por formulario.", "Registro")
    assert datos == {"monto": 20.0, "moneda": "Bs", "concepto": "formulario"}
    assert chat.ultimo_schema["additionalProperties"] is False


def test_extraer_costo_sin_monto_devuelve_none():
    assert extraer_costo(FakeChat({"monto": None, "moneda": None, "concepto": None}), "d", "r") is None
    assert extraer_costo(FakeChat(None), "d", "r") is None
