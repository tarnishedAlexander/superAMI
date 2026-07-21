from typing import Literal

# Calibrados el 2026-07-14 con tests/eval_retrieval.py sobre el dataset completo
# (1,739 trámites, bge-m3 @ 1024 dims). En esa corrida: los top-1 correctos con
# gap >= 0.028 eran todos aciertos y los top-1 incorrectos tenían gap <= 0.026,
# así que 0.03 separa ambos grupos; el d1 máximo de un acierto fue 0.511, por eso
# 0.52 (la consulta sin match más lejana, d1 0.641, queda gateada). Los gaps chicos
# (<0.03) suelen ser variantes legítimas del mismo trámite (original vs. duplicado)
# donde preguntar es mejor UX que adivinar.
UMBRAL_GAP = 0.005
UMBRAL_DISTANCIA_MAX = 0.4


def evaluar_confianza(
    distancias: list[float],
    umbral_gap: float = UMBRAL_GAP,
    umbral_distancia_max: float = UMBRAL_DISTANCIA_MAX,
) -> Literal["claro", "ambiguo", "vacio", "lejano"]:
    """Gate determinista sobre las distancias coseno del retrieval (ascendentes).

    - "lejano": el mejor match está más allá de umbral_distancia_max — el trámite
      probablemente no está en la DB; preguntar no lo va a hacer aparecer.
    - "ambiguo": gap chico entre top-1 y top-2 con d1 razonable — ambigüedad
      genuina entre candidatos, corresponde aclarar.
    """
    if not distancias:
        return "vacio"
    if distancias[0] > umbral_distancia_max:
        return "lejano"
    if len(distancias) == 1:
        return "claro"
    if distancias[1] - distancias[0] >= umbral_gap:
        return "claro"
    return "ambiguo"
