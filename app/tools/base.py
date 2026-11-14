"""Contrato de una herramienta (fase 1: contexto dinámico por *function calling*).

Una herramienta declara su nombre, su descripción y el schema de sus argumentos (lo
que ve el modelo) y sabe ejecutarse. El modelo decide si la usa; el servicio la
ejecuta y devuelve el resultado como **dato**.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class ToolError(Exception):
    """Fallo controlado: argumentos inválidos, servicio caído, respuesta inesperada."""


class Tool(Protocol):
    name: str
    description: str
    parameters: dict

    async def run(self, args: dict) -> str: ...


@dataclass(frozen=True)
class ToolResult:
    tool: str
    args: dict = field(default_factory=dict)
    content: str = ""
    ok: bool = True
    error: str | None = None
    ms: float = 0

    def as_context(self) -> dict:
        """Forma que entra en ``<tool_results>`` del prompt (DynamicContext.tool_results)."""
        return {"tool": self.tool, "content": self.content if self.ok else f"Error: {self.error}", "error": not self.ok}


def function_schema(tool: Tool) -> dict:
    """La herramienta tal como se ofrece al modelo (formato OpenAI, vía LiteLLM)."""
    return {
        "type": "function",
        "function": {"name": tool.name, "description": tool.description, "parameters": tool.parameters},
    }
