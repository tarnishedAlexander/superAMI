from api.prompts import schema_relaciones, usuario_relaciones


def test_schema_restringe_ids_y_tipos():
    schema = schema_relaciones([10, 20])
    item = schema["properties"]["relaciones"]["items"]
    assert item["properties"]["id"]["enum"] == [10, 20]
    assert "ninguna" in item["properties"]["tipo"]["enum"]


def test_usuario_relaciones_incluye_base_y_candidatos():
    base = {"id": 1, "nombre": "REGISTRO DE COMERCIO", "descripcion": "x" * 500}
    candidatos = [{"id": 2, "nombre": "NIT", "descripcion": "obtener nit"}]
    texto = usuario_relaciones(base, candidatos)
    assert "REGISTRO DE COMERCIO" in texto
    assert "[2] NIT" in texto
    assert len(texto) < 2500  # descripciones truncadas a 300 chars
