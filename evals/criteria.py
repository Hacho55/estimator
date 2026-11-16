"""Criterios del juez (GEval). Compartidos por la suite y por la calibración: una sola definición."""

SCOPE_COHERENCE = (
    "Evalúa si los componentes, el trabajo y los riesgos de la salida corresponden al alcance descrito "
    "en la entrada. Penaliza trabajo o riesgos que la entrada no implica y omisiones de piezas que la "
    "entrada pide de forma explícita."
)

TIER_FIT = {
    "developer": (
        "La salida sirve a un equipo de ingeniería: desglose por componente técnico, riesgos técnicos "
        "y supuestos de stack."
    ),
    "pm": "La salida sirve a quien dirige la entrega: fases, hitos con entregable, equipo, duración y bloqueos.",
    "executive": (
        "La salida sirve a dirección: cifras de cabecera, confianza honesta, tres riesgos de negocio "
        "y una recomendación accionable, sin jerga técnica."
    ),
}
