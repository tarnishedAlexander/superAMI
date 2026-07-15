from api.conversations import ConversationStore


def test_append_con_tipo_y_conteo_de_aclaraciones():
    store = ConversationStore()
    cid = store.get_or_create(None)
    store.append(cid, "user", "papel del carro")
    store.append(cid, "assistant", "¿Te referís a A o B?", tipo="clarification")
    store.append(cid, "user", "el de propiedad")
    assert store.contar_aclaraciones(cid) == 1


def test_mensajes_no_expone_tipo():
    store = ConversationStore()
    cid = store.get_or_create(None)
    store.append(cid, "assistant", "hola", tipo="answer")
    assert store.mensajes(cid) == [{"role": "assistant", "content": "hola"}]


def test_tipo_default_es_el_role():
    store = ConversationStore()
    cid = store.get_or_create(None)
    store.append(cid, "user", "hola")
    assert store.contar_aclaraciones(cid) == 0
