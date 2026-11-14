"""Fase de investigación (fase 1, D1): bucle de *tool calling* antes de estimar.

Va en una llamada aparte de la estimación para no tocar su camino (JSON con schema,
streaming, cachés): primero el modelo pide herramientas, el servicio las ejecuta y sus
resultados entran como datos en ``<tool_results>`` del mensaje del actor.

Topes: ``TOOLS_MAX_ITERATIONS`` vueltas y ``TOOLS_MAX_CALLS`` invocaciones. Un fallo de
herramienta es un resultado con ``error``, no un 5xx: la estimación sigue sin ella.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field

import structlog

from app.config import get_settings
from app.observability import traced_span
from app.prompts.loader import render_research_prompt
from app.schemas.estimation import UsageInfo
from app.services import llm_client
from app.services.errors import ServiceError
from app.services.guardrails import guard_dynamic_text
from app.tools import REGISTRY
from app.tools.base import ToolError, ToolResult, function_schema

log = structlog.get_logger(__name__)


@dataclass
class ResearchOutcome:
    results: list[ToolResult] = field(default_factory=list)
    usage: list[UsageInfo] = field(default_factory=list)
    cost_usd: float | None = None
    latency_ms: float = 0
    calls: int = 0  # Llamadas al modelo de la fase de investigación.


def _usage(result: llm_client.LLMResult) -> UsageInfo:
    return UsageInfo(
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        total_tokens=result.total_tokens,
        cached_tokens=result.cached_tokens,
        cache_creation_tokens=result.cache_creation_tokens,
    )


async def _execute(name: str, raw_args: str) -> ToolResult:
    started = time.perf_counter()
    tool = REGISTRY.get(name)
    try:
        if tool is None:
            raise ToolError(f"Herramienta desconocida: {name}.")
        args = json.loads(raw_args or "{}")
        if not isinstance(args, dict):
            raise ToolError("Los argumentos no son un objeto JSON.")
        timeout = get_settings().TOOL_TIMEOUT_SECONDS
        content = await asyncio.wait_for(tool.run(args), timeout=timeout)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — una herramienta nunca tumba el turno.
        if isinstance(exc, TimeoutError):
            reason = "tiempo agotado"
        elif isinstance(exc, (ToolError, ValueError)):
            reason = str(exc) or type(exc).__name__
        else:
            reason = f"fallo inesperado ({type(exc).__name__})"
        log.warning("tool_failed", tool=name, reason=reason)
        return ToolResult(tool=name, ok=False, error=reason, ms=(time.perf_counter() - started) * 1000)
    limit = get_settings().TOOL_RESULT_MAX_CHARS
    # Dato externo: pasa el guardrail antes de volver al modelo (también al de investigación).
    safe = guard_dynamic_text(str(content)[:limit], source=f"tool:{name}")
    return ToolResult(tool=name, args=args, content=safe, ms=(time.perf_counter() - started) * 1000)


async def run_research(*, tools: list[str], transcript: str, memory: dict | None) -> ResearchOutcome:
    outcome = ResearchOutcome()
    if not tools:
        return outcome
    settings = get_settings()
    system, user = render_research_prompt(transcript=transcript, memory=json.dumps(memory or {}, ensure_ascii=False))
    messages: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    schemas = [function_schema(REGISTRY[name]) for name in tools]
    started = time.perf_counter()
    for iteration in range(1, settings.TOOLS_MAX_ITERATIONS + 1):
        try:
            turn = await llm_client.complete_with_tools(
                messages, schemas, name=f"research-{iteration}", model=settings.RESEARCH_MODEL.strip() or None
            )
        except ServiceError as exc:
            # Lo ya obtenido (resultados y gasto) se conserva; la estimación sigue con ello.
            log.warning("research_model_failed", iteration=iteration, reason=exc.message)
            break
        outcome.calls += 1
        outcome.usage.append(_usage(turn.result))
        if turn.result.cost_usd is not None:
            outcome.cost_usd = (outcome.cost_usd or 0) + turn.result.cost_usd
        if not turn.tool_calls:
            break
        messages.append(turn.assistant_message)
        for call in turn.tool_calls:
            if len(outcome.results) >= settings.TOOLS_MAX_CALLS or call.name not in tools:
                # Fuera de tope o no ofrecida: se responde al modelo, pero no se ejecuta.
                result = ToolResult(tool=call.name, ok=False, error="No disponible en este turno.")
            else:
                with traced_span(f"tool:{call.name}") as span:
                    result = await _execute(call.name, call.arguments)
                    if span is not None:
                        span.update(input=result.args, output={"ok": result.ok, "ms": round(result.ms, 1), "chars": len(result.content)})
                outcome.results.append(result)
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": result.content if result.ok else f"Error: {result.error}"}
            )
        if len(outcome.results) >= settings.TOOLS_MAX_CALLS:
            break
    outcome.latency_ms = (time.perf_counter() - started) * 1000
    log.info("research_done", calls=outcome.calls, tools=[r.tool for r in outcome.results], ok=[r.ok for r in outcome.results])
    return outcome
