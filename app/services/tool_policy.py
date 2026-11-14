"""Qué herramientas se ofrecen en un turno. Sin LLM.

Tres filtros, en orden (fase 1: «actívalas con criterio, no por defecto»):

1. El tier las declara (``TIER_CONFIG.tools``, D9).
2. Están disponibles: configuradas y no apagadas por su interruptor global.
3. El turno da motivos:
   - ``web_search``: solo si el turno habla de algo sensible al tiempo (versiones,
     precios, benchmarks, disponibilidad de librerías). Para patrones o prácticas
     generales el modelo ya sabe y buscar solo añade ruido y latencia.
   - ``find_similar_projects``: en el primer turno de la sesión o cuando el turno trae
     alcance nuevo (adjuntos o texto largo). Un ajuste pequeño no lo necesita.

Si no queda ninguna, no hay fase de investigación: ni llamada ni latencia extra.
"""

from __future__ import annotations

import re

from app.tiers import TierConfig
from app.tools import available

TIME_SENSITIVE = re.compile(
    r"\b(precio|precios|coste de licencia|tarifa de|pricing|versi[oó]n|release|benchmark|"
    r"rendimiento de|deprecad|obsolet|soporte de|20\d\d|novedad|reciente|nuevo framework|"
    r"bun|deno|saas)\b",
    re.IGNORECASE,
)
NEW_SCOPE_CHARS = 400


def offered_tools(
    config: TierConfig | None,
    *,
    transcript: str,
    has_prior_turns: bool,
    has_attachments: bool,
) -> list[str]:
    if config is None or not config.research:
        return []
    offered: list[str] = []
    for name in config.tools:
        if not available(name):
            continue
        if name == "web_search" and TIME_SENSITIVE.search(transcript):
            offered.append(name)
        elif name == "find_similar_projects" and (
            not has_prior_turns or has_attachments or len(transcript) >= NEW_SCOPE_CHARS
        ):
            offered.append(name)
    return offered
