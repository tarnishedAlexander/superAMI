from api.prompts import system_de_sintesis


def _tramite():
    return {"id": 1, "nombre": "TRAMITE X", "enlaces": [], "distancia": 0.2}


def test_sintesis_instruye_que_hacer_con_datos_ausentes():
    system = system_de_sintesis(_tramite())
    assert "SÍ tiene la ficha" in system


def test_sintesis_incluye_el_tramite():
    assert '"nombre": "TRAMITE X"' in system_de_sintesis(_tramite())
