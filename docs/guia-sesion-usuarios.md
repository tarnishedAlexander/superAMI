# Guía: sesión de prueba con usuarios reales (cierre del MVP)

Objetivo: 3-5 personas, ~15 min cada una, frases reales para re-calibrar.

## Protocolo

1. Levantar el server local y verificar `/health`.
2. Consigna a la persona (no mostrar ejemplos antes — sesgan el lenguaje):
   "Preguntale al asistente sobre 3 trámites que hayas hecho o tengas pendientes,
   como le escribirías a un conocido por WhatsApp."
3. No intervenir salvo bloqueo total. Anotar: reacciones, respuestas confusas,
   aclaraciones que molestaron.
4. Al final preguntar: ¿la respuesta te habría servido en la vida real? ¿qué faltó?

## Después de la sesión

1. Exportar las frases: `SELECT ts, mensaje, veredicto, respuesta_tipo FROM consultas_log ORDER BY ts DESC;`
2. Etiquetar cada frase (directa/ambigua/no_satisfacible) y agregarlas a `tests/eval_dataset.py`.
3. Re-correr `tests/verificar_eval.py` y `tests/calibrar_gate.py`; ajustar umbrales si cambia la recomendación.
4. Verificar los criterios de éxito del spec (abajo) y anotar resultados en DECISIONS.md.

## Criterios de éxito (spec 2026-07-14)

- [ ] hit@5 >= 90% en directas
- [ ] cero "claro" incorrecto
- [ ] <= 25% de directas caen en aclaración innecesaria
- [ ] 100% de no satisfacibles sin inventar datos
