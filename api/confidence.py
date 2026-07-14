from typing import Literal

# Valores de partida — calibrar con tests/eval_retrieval.py (ver Task 9).
UMBRAL_GAP = 0.05
UMBRAL_DISTANCIA_MAX = 0.55


def evaluar_confianza(
    distancias: list[float],
    umbral_gap: float = UMBRAL_GAP,
    umbral_distancia_max: float = UMBRAL_DISTANCIA_MAX,
) -> Literal["claro", "ambiguo", "vacio"]:
    """Gate determinista sobre las distancias coseno del retrieval (ascendentes)."""
    if not distancias:
        return "vacio"
    if distancias[0] > umbral_distancia_max:
        return "ambiguo"
    if len(distancias) == 1:
        return "claro"
    if distancias[1] - distancias[0] >= umbral_gap:
        return "claro"
    return "ambiguo"
