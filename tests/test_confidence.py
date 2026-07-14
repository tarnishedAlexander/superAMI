from api.confidence import evaluar_confianza


def test_vacio():
    assert evaluar_confianza([]) == "vacio"


def test_claro_con_gap_grande():
    assert evaluar_confianza([0.20, 0.40, 0.45]) == "claro"


def test_ambiguo_con_gap_chico():
    assert evaluar_confianza([0.30, 0.32, 0.45]) == "ambiguo"


def test_ambiguo_si_top1_esta_lejos():
    # aunque el gap sea grande, si el mejor resultado está lejos no hay confianza
    assert evaluar_confianza([0.80, 0.99]) == "ambiguo"


def test_claro_resultado_unico_cercano():
    assert evaluar_confianza([0.20]) == "claro"


def test_umbral_es_parametrizable():
    assert evaluar_confianza([0.30, 0.32], umbral_gap=0.01) == "claro"
