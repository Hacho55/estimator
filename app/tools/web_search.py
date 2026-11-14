"""Búsqueda web con Tavily (D4): un servicio pensado para LLMs, independiente del proveedor.

Solo para lo que el modelo no sabe por su fecha de corte: versiones recientes, precios de
SaaS, benchmarks, estado de una librería. Devuelve título, URL y un fragmento recortado.
"""

from __future__ import annotations

import json

import httpx

from app.config import get_settings
from app.tools import net
from app.tools.base import ToolError

TAVILY_URL = "https://api.tavily.com/search"
SNIPPET_CHARS = 500


class WebSearch:
    name = "web_search"
    description = (
        "Busca en la web información reciente que el modelo no puede conocer: versiones de frameworks "
        "de los últimos meses, precios de servicios SaaS o cloud, benchmarks, estado de una librería. "
        "No la uses para patrones de arquitectura ni prácticas generales."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Consulta concreta, con producto y año si importa."},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 5},
        },
        "required": ["query"],
    }

    async def run(self, args: dict) -> str:
        query = str(args.get("query") or "").strip()
        if not query:
            raise ToolError("Falta query.")
        try:
            max_results = min(5, max(1, int(args.get("max_results") or 3)))
        except (TypeError, ValueError) as exc:
            raise ToolError("max_results no es un número.") from exc
        key = get_settings().TAVILY_API_KEY.get_secret_value()
        try:
            async with net.client() as client:
                response = await client.post(
                    TAVILY_URL,
                    json={"query": query, "max_results": max_results, "search_depth": "basic"},
                    headers={"Authorization": f"Bearer {key}"},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ToolError(f"Tavily no respondió bien: {type(exc).__name__}") from exc
        items = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise ToolError("Tavily devolvió una forma inesperada.")
        results = [
            {
                "title": str(item.get("title", "")),
                "url": str(item.get("url", "")),
                "snippet": str(item.get("content") or "")[:SNIPPET_CHARS],
            }
            for item in items
            if isinstance(item, dict)
        ]
        return json.dumps({"query": query, "results": results}, ensure_ascii=False)
