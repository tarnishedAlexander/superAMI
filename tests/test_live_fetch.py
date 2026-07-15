from api.live_fetch import extraer_texto, primera_url


def test_primera_url_acepta_strings_y_dicts():
    assert primera_url(["https://a.gob.bo/x"]) == "https://a.gob.bo/x"
    assert primera_url([{"url": "https://b.gob.bo/y"}]) == "https://b.gob.bo/y"
    assert primera_url([{"enlace": "https://c.gob.bo/z"}]) == "https://c.gob.bo/z"
    assert primera_url([{"titulo": "sin url"}, "https://d.gob.bo"]) == "https://d.gob.bo"
    assert primera_url([]) is None
    assert primera_url(None) is None
    assert primera_url(["ftp://no-http"]) is None


def test_extraer_texto_limpia_html():
    html = """<html><head><style>body{color:red}</style></head>
    <body><nav>menu</nav><script>var x=1;</script>
    <p>Requisitos   del  trámite</p><p>Costo: 50 Bs</p></body></html>"""
    texto = extraer_texto(html)
    assert "Requisitos del trámite" in texto
    assert "Costo: 50 Bs" in texto
    assert "var x=1" not in texto
    assert "menu" not in texto


def test_extraer_texto_trunca():
    texto = extraer_texto("<p>" + "palabra " * 5000 + "</p>")
    assert len(texto) <= 8000
