from datetime import date

from ingest.mapper import (
    aplanar_requisitos,
    mapear_canal,
    mapear_costo,
    mapear_tramite,
    parsear_fecha,
    strip_html,
    texto_para_embedding,
)


def _record_base(**overrides):
    record = {
        "id": "1002",
        "estado": "PUBLICADO",
        "fechaActualización": "22/10/2025",
        "nombre": "SOLICITUD DE COPIAS SIMPLES",
        "slug": "solicitud-de-copias-simples",
        "descripcion": "Copias simples de documentos aduaneros.",
        "marcoLegal": "RD 01-096-24",
        "palabrasClave": None,
        "esPresencial": False,
        "esVirtual": True,
        "entidad": {"nombre": "Aduana Nacional", "sigla": "AN", "slug": "aduana-nacional", "urlSitioWeb": "https://www.aduana.gob.bo/"},
        "resultado": "Registro",
        "enlaces": [],
        "categorias": [{"nombre": "Empresas", "slug": "empresas"}],
        "eventosVida": ["Empleo"],
        "documentos": ["COPIA SIMPLE"],
        "ubicaciones": [],
        "modalidades": [
            {
                "tipo": "VIRTUAL",
                "url": "https://jarembae.aduana.gob.bo/",
                "urlSeguimiento": None,
                "publico": [
                    {
                        "tieneCosto": True,
                        "procedimiento": "<p><strong>Paso 1</strong></p>",
                        "costos": [{"conceptoPago": "158", "costo": "1.00", "moneda": "UFV"}],
                        "requisitos": [{"comentario": None, "nombre": "Carta de Solicitud", "entidad": None, "entidadActiva": True}],
                    }
                ],
            }
        ],
    }
    record.update(overrides)
    return record


def test_strip_html():
    assert strip_html("<p><strong>Paso  1</strong> ir</p>") == "Paso 1 ir"
    assert strip_html(None) is None


def test_parsear_fecha():
    assert parsear_fecha("22/10/2025") == date(2025, 10, 22)
    assert parsear_fecha("basura") is None


def test_mapear_canal():
    assert mapear_canal({"esPresencial": True, "esVirtual": True}) == "ambos"
    assert mapear_canal({"esPresencial": False, "esVirtual": True}) == "virtual"
    assert mapear_canal({"esPresencial": False, "esVirtual": False}) is None


def test_mapear_costo_directo():
    costo = mapear_costo(_record_base())
    assert costo == {
        "costo_monto": 1.0,
        "costo_moneda": "UFV",
        "costo_concepto": "158",
        "costo_es_gratuito": False,
        "necesita_llm": False,
    }


def test_mapear_costo_gratuito():
    record = _record_base()
    record["modalidades"][0]["publico"][0].update({"tieneCosto": False, "costos": []})
    costo = mapear_costo(record)
    assert costo["costo_es_gratuito"] is True
    assert costo["necesita_llm"] is False
    assert costo["costo_monto"] is None


def test_mapear_costo_necesita_llm():
    record = _record_base()
    record["modalidades"][0]["publico"][0]["costos"] = []
    costo = mapear_costo(record)
    assert costo["costo_es_gratuito"] is False
    assert costo["necesita_llm"] is True


def test_aplanar_requisitos_dedupe():
    record = _record_base()
    record["modalidades"].append(
        {"tipo": "PRESENCIAL", "url": None, "urlSeguimiento": None,
         "publico": [{"tieneCosto": False, "costos": [],
                      "requisitos": [{"nombre": "Carta de Solicitud", "comentario": "<b>original</b>"},
                                     {"nombre": "Cédula de identidad", "comentario": None}]}]}
    )
    requisitos = aplanar_requisitos(record)
    nombres = [r["nombre"] for r in requisitos]
    assert nombres == ["Carta de Solicitud", "Cédula de identidad"]
    assert requisitos[0]["comentario"] == "original"


def test_mapear_tramite_completo():
    fila = mapear_tramite(_record_base())
    assert fila["id"] == 1002
    assert fila["sinonimos"] == []
    assert fila["canal"] == "virtual"
    assert fila["digitalizado"] is True
    assert fila["eventos"] == ["Empleo"]
    assert fila["categorias"] == [{"slug": "empresas", "nombre": "Empresas"}]
    assert fila["entidad"]["sitio_web"] == "https://www.aduana.gob.bo/"
    assert fila["modalidades"][0]["publico"][0]["procedimiento"] == "Paso 1"
    assert fila["last_updated"] == date(2025, 10, 22)


def test_texto_para_embedding():
    fila = mapear_tramite(_record_base(palabrasClave=["copias", "aduana"]))
    texto = texto_para_embedding(fila)
    assert "SOLICITUD DE COPIAS SIMPLES" in texto
    assert "copias aduana" in texto
