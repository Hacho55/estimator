"""Herramientas que el modelo puede pedir. Registro por nombre y disponibilidad.

Una herramienta está *disponible* si tiene su configuración (clave, URL); *se ofrece*
si además el tier la declara y la política la activa (``app/services/tool_policy.py``).
"""

from __future__ import annotations

from app.config import get_settings
from app.tools.base import Tool
from app.tools.similar_projects import SimilarProjects
from app.tools.web_search import WebSearch

REGISTRY: dict[str, Tool] = {tool.name: tool for tool in (WebSearch(), SimilarProjects())}


def available(name: str) -> bool:
    """Configurada y no apagada por su interruptor global."""
    settings = get_settings()
    if name == "web_search":
        return settings.TOOLS_WEB_SEARCH and bool(settings.TAVILY_API_KEY.get_secret_value())
    if name == "find_similar_projects":
        return (
            settings.TOOLS_SIMILAR_PROJECTS
            and bool(settings.BUSINESS_BACKEND_URL)
            and bool(settings.BUSINESS_BACKEND_TOKEN.get_secret_value())
        )
    return False
