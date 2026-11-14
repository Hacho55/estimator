"""Proyectos similares del histórico, vía el backend de negocio (fase 1, §4).

El estimador no conoce el esquema de proyectos ni tiene credenciales de su BBDD: llama a
``POST {BUSINESS_BACKEND_URL}/api/internal/similar_projects`` con un token de servicio y
recibe métricas agregadas (horas reales, desviación, riesgos materializados). Qué cuenta
como «similar» lo decide el backend.
"""

from __future__ import annotations

import json

import httpx

from app.config import get_settings
from app.tools import net
from app.tools.base import ToolError


class SimilarProjects:
    name = "find_similar_projects"
    description = (
        "Busca proyectos ya cerrados de la empresa con alcance, tecnologías y equipo parecidos. "
        "Devuelve sus horas estimadas y reales, la desviación y los riesgos que se materializaron. "
        "Úsala para calibrar la estimación con datos propios."
    )
    parameters = {
        "type": "object",
        "properties": {
            "technologies": {"type": "array", "items": {"type": "string"}},
            "scope_summary": {"type": "string", "description": "El alcance en una o dos frases."},
            "team_size": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "required": ["technologies", "scope_summary"],
    }

    async def run(self, args: dict) -> str:
        settings = get_settings()
        scope = str(args.get("scope_summary") or "").strip()
        if len(scope) < 3:
            raise ToolError("Falta scope_summary.")
        technologies = args.get("technologies") or []
        if not isinstance(technologies, list):
            raise ToolError("technologies debe ser una lista.")
        team_size = args.get("team_size")
        if team_size is not None and (not isinstance(team_size, int) or not 1 <= team_size <= 50):
            raise ToolError("team_size debe ser un entero entre 1 y 50.")
        body = {
            "technologies": [str(item) for item in technologies][:30],
            "scope_summary": scope[:2000],
            "team_size": team_size,
        }
        url = f"{settings.BUSINESS_BACKEND_URL.rstrip('/')}/api/internal/similar_projects"
        token = settings.BUSINESS_BACKEND_TOKEN.get_secret_value()
        try:
            async with net.client() as client:
                response = await client.post(url, json=body, headers={"Authorization": f"Bearer {token}"})
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ToolError(f"El backend de negocio no respondió bien: {type(exc).__name__}") from exc
        projects = payload.get("projects") if isinstance(payload, dict) else None
        if not isinstance(projects, list):
            raise ToolError("El backend de negocio devolvió una forma inesperada.")
        return json.dumps({"projects": projects}, ensure_ascii=False)
