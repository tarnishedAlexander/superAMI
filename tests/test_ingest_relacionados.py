from ingest.relacionados import clasificar_tramite


def _candidato(id_):
    return {"id": id_, "nombre": f"T{id_}", "descripcion": "d"}


def _base():
    return {"id": 1, "nombre": "BASE", "descripcion": None}


class _FakeChatAnidado:
    """Forma pedida por schema_relaciones: {"relaciones": [{"id", "tipo"}, ...]}."""

    def complete_json(self, *, system, messages, schema, max_tokens=500):
        return {"relaciones": [{"id": 1012, "tipo": "alternativa"}]}


class _FakeChatPlano:
    """Forma que a veces devuelve NIM con guided_json en schemas anidados: objeto plano id->tipo."""

    def complete_json(self, *, system, messages, schema, max_tokens=500):
        return {"1012": "alternativa", "1013": "ninguna"}


class _FakeChatInvalido:
    def complete_json(self, *, system, messages, schema, max_tokens=500):
        return {"9999": "alternativa", "1012": "tipo_invalido"}


def test_acepta_forma_anidada_esperada():
    relaciones = clasificar_tramite(_FakeChatAnidado(), _base(), [_candidato(1012)])
    assert relaciones == [{"id": 1012, "tipo": "alternativa"}]


def test_tolera_forma_plana_id_tipo():
    relaciones = clasificar_tramite(_FakeChatPlano(), _base(), [_candidato(1012), _candidato(1013)])
    assert {"id": 1012, "tipo": "alternativa"} in relaciones
    assert {"id": 1013, "tipo": "ninguna"} in relaciones


def test_ids_o_tipos_invalidos_se_descartan():
    relaciones = clasificar_tramite(_FakeChatInvalido(), _base(), [_candidato(1012)])
    assert relaciones == []


def test_sin_candidatos_no_llama_al_chat():
    class ChatQueExplota:
        def complete_json(self, *, system, messages, schema, max_tokens=500):
            raise AssertionError("no debería llamarse sin candidatos")

    assert clasificar_tramite(ChatQueExplota(), _base(), []) == []
