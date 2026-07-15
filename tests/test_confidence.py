from api.confidence import evaluar_confianza


def test_vacio():
    assert evaluar_confianza([]) == "vacio"


def test_claro_con_gap_grande():
    assert evaluar_confianza([0.20, 0.40, 0.45], umbral_gap=0.03, umbral_distancia_max=0.52) == "claro"


def test_ambiguo_con_gap_chico():
    assert evaluar_confianza([0.30, 0.32, 0.45], umbral_gap=0.03, umbral_distancia_max=0.52) == "ambiguo"


def test_lejano_si_top1_supera_distancia_max():
    # antes esto era "ambiguo"; ahora es señal de que el trámite no está en la DB
    assert evaluar_confianza([0.80, 0.99], umbral_gap=0.03, umbral_distancia_max=0.52) == "lejano"


def test_claro_resultado_unico_cercano():
    assert evaluar_confianza([0.20], umbral_gap=0.03, umbral_distancia_max=0.52) == "claro"


def test_lejano_resultado_unico_lejos():
    assert evaluar_confianza([0.70], umbral_gap=0.03, umbral_distancia_max=0.52) == "lejano"


def test_umbral_es_parametrizable():
    assert evaluar_confianza([0.30, 0.32], umbral_gap=0.01, umbral_distancia_max=0.52) == "claro"
