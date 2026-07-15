from datetime import date

from ingest.sync import diff_registros


def _registro(id_, fecha="01/01/2026"):
    return {"id": id_, "fechaActualización": fecha}


def _estado(last_updated, activo=True):
    return {"last_updated": last_updated, "activo": activo}


def test_registro_nuevo_entra_al_diff():
    cambiados, bajas = diff_registros([_registro(1)], {})
    assert [r["id"] for r in cambiados] == [1]
    assert bajas == set()


def test_registro_sin_cambios_no_entra():
    estado = {1: _estado(date(2026, 1, 1))}
    cambiados, _ = diff_registros([_registro(1, "01/01/2026")], estado)
    assert cambiados == []


def test_registro_modificado_entra():
    estado = {1: _estado(date(2026, 1, 1))}
    cambiados, _ = diff_registros([_registro(1, "08/02/2026")], estado)
    assert [r["id"] for r in cambiados] == [1]


def test_registro_en_db_sin_fecha_entra():
    estado = {1: _estado(None)}
    cambiados, _ = diff_registros([_registro(1)], estado)
    assert [r["id"] for r in cambiados] == [1]


def test_sin_fecha_en_ambos_lados_no_entra():
    # sin señal de cambio: no re-procesar cada semana (idempotencia)
    estado = {1: _estado(None)}
    cambiados, _ = diff_registros([_registro(1, fecha=None)], estado)
    assert cambiados == []


def test_baja_detectada_por_ausencia():
    estado = {1: _estado(date(2026, 1, 1)), 2: _estado(date(2026, 1, 1))}
    _, bajas = diff_registros([_registro(1, "01/01/2026")], estado)
    assert bajas == {2}


def test_inactivo_que_reaparece_entra_para_reactivarse():
    estado = {1: _estado(date(2026, 1, 1), activo=False)}
    cambiados, bajas = diff_registros([_registro(1, "01/01/2026")], estado)
    assert [r["id"] for r in cambiados] == [1]
    assert bajas == set()


def test_inactivo_ausente_no_es_baja_de_nuevo():
    estado = {1: _estado(date(2026, 1, 1), activo=False)}
    _, bajas = diff_registros([], estado)
    assert bajas == set()
