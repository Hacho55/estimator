"""Caso de uso: una transcripción (y, si viene, un historial) entra, una estimación sale.

No conoce HTTP ni conversaciones. El historial es una lista de mensajes que el
router decide pasar. Sin historial, la llamada es de un solo turno.

Observabilidad, mezclada aquí a propósito y marcada en cada sitio: estimation_trace,
traced_span, record_scores, set_trace_io y log_event. Ninguna cambia la estimación.
La implementación vive en app/observability.py.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from app.config import derive_provider, get_settings
from app.context.preprocessing import build_context_block, get_context_block
from app.evaluation.metrics import collect_metrics
from app.evaluation.properties import Subject
from app.observability import (
    current_trace_id,
    estimation_trace,
    log_event,
    log_llm_completed,
    log_llm_started,
    record_scores,
    set_trace_io,
    traced_span,
)
from app.prompts.loader import UnknownPromptVersionError
from app.schemas.context import ContextBlock, ContextInfo
from app.schemas.conversation import ChatMessage
from app.schemas.estimation import (
    ContextMeta,
    DetailLevel,
    EstimationResponse,
    OutputFormat,
    UsageInfo,
    ValidationIssue,
)
from app.schemas.review import ReviewTrace
from app.services import llm_client
from app.services.errors import (
    ContextBudgetExceededError,
    InvalidLLMOutputError,
    LLMOutputTruncatedError,
    LLMUnavailableError,
    PromptVersionError,
    ServiceError,
)
from app.services.guardrails import guard_dynamic_text, guard_input, guard_output, redact_pii
from app.services.postprocessor import (
    parse_turn,
    preview_markdown,
    render_markdown,
    to_history_content,
    validate_and_fix,
)
from app.services.prompt_builder import (
    DEFAULT_PROMPT_VERSION,
    DynamicContext,
    apply_cache_strategy,
    build_messages,
    build_preview,
    build_response_format,
    cacheable_text,
    ensure_alternation,
    prompt_version,
    repair_instruction,
    system_text_of,
)
from app.services.research import ResearchOutcome, run_research
from app.services.review_loop import ESTIMATION_KINDS, run_review
from app.services.tier_views import (
    build_view,
    corrected_draft,
    parse_tier_turn,
    redact_model,
    render_view_markdown,
    to_estimation_draft,
)
from app.services.tier_views import history_content as tier_history_content
from app.services.tool_policy import offered_tools
from app.state.conversations import select_history
from app.state.response_cache import CachedCompletion, cache_key, lookup, store
from app.state.semantic_cache import (
    bucket_id,
    emit_semantic_check,
    lookup_semantic,
    store_semantic,
)
from app.state.sessions import slide_history
from app.tiers import TierConfig, resolve_tier_config
from app.tokens import CACHE_MIN_TOKENS, build_token_budget, count_tokens

GATEWAY = "litellm"
# Sesión 3 · routing: por debajo de esto, y con un solo requisito técnico, cabe el modelo barato.
_SHORT_TRANSCRIPTION_CHARS = 400
_TECHNICAL_MARKERS = (
    "api",
    "aplicaci",
    "app",
    "autentic",
    "backend",
    "base de datos",
    "crm",
    "endpoint",
    "erp",
    "frontend",
    "integraci",
    "login",
    "movil",
    "móvil",
    "pago",
    "panel",
    "usuario",
    "webhook",
)


def choose_model(transcription: str, *, has_history: bool) -> str:
    """Sesión 3 · routing: el modelo potente es el defecto. Solo un texto corto y simple baja al reserva."""
    settings = get_settings()
    strong = settings.LLM_MODEL
    cheap = settings.LLM_FALLBACK_MODEL.strip()
    if settings.LLM_ROUTING != "complexity" or has_history or not cheap or cheap == strong:
        return strong
    if len(transcription) > _SHORT_TRANSCRIPTION_CHARS:
        return strong
    lowered = transcription.lower()
    hits = sum(marker in lowered for marker in _TECHNICAL_MARKERS)
    if hits > 1:
        return strong
    return cheap


@dataclass
class ContextOverrides:
    format: str | None = None
    detail_level: str | None = None  # Nivel del bloque CAG (summary/standard/full).
    max_examples: int | None = None
    model: str | None = None
    prompt_cache: str | None = None
    prompt_version: str | None = None  # Carpeta de plantilla (v1, v2).
    form_detail_level: object | None = None  # DetailLevel del formulario.
    output_format: object | None = None
    project_type: object | None = None
    project_metadata: str = ""  # Bloque JSON de v3.
    dynamic: DynamicContext | None = None  # Memoria, adjuntos, herramientas y crítico (v4).
    session_window: bool = False  # Sesión: recorta por pares viejos, sin anclas.
    tier: str | None = None  # Perfil del llamante (app/tiers.py). None: contrato clásico de v1/v2.
    call_name: str = "generate-estimate"  # Nombre de la llamada en trazas (actor-2 en la revisión).
    defer_cache: bool = False  # Con revisión, solo se cachea la salida final aceptada.


@dataclass
class TurnResult:
    kind: str
    message: str | None
    estimation_markdown: str | None
    estimation: object | None
    warnings: list[ValidationIssue]
    history_content: str
    prompt_version: str
    context: ContextMeta
    token_budget: object
    usage: UsageInfo
    cost_usd: float | None
    latency_ms: float
    model: str
    provider: str
    trace_id: str | None
    cache_hit: bool = False
    cache_kind: str | None = None
    cache_similarity: float | None = None
    finish_reason: str | None = None
    dropped_turns: int = 0
    repairs: int = 0
    scrubbed_input: str = ""
    tier: str | None = None
    view: object | None = None  # Vista del tier para el cliente (app/schemas/tiers.py).
    attachments_used: list[dict] = field(default_factory=list)  # Adjuntos ya tachados que vio el modelo.
    cache_key: str | None = None
    review: ReviewTrace | None = None  # Auditoría Actor-Critic-Boss, si hubo revisión.
    tools: list[dict] = field(default_factory=list)  # Herramientas usadas: tool, ok, ms, error.
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_estimation_response(self) -> EstimationResponse:
        if self.estimation_markdown is None or self.estimation is None:
            raise InvalidLLMOutputError("La respuesta no contiene una estimación.")
        return EstimationResponse(
            estimation=self.estimation_markdown,
            model=self.model,
            provider=self.provider,
            gateway=GATEWAY,
            estimation_structured=self.estimation,  # type: ignore[arg-type]
            warnings=self.warnings,
            prompt_version=self.prompt_version,
            context=self.context,
            token_budget=self.token_budget,  # type: ignore[arg-type]
            usage=self.usage,
            cost_usd=self.cost_usd,
            latency_ms=self.latency_ms,
            cache_hit=self.cache_hit,
            cache_kind=self.cache_kind,
            cache_similarity=self.cache_similarity,
            trace_id=self.trace_id,
            created_at=self.created_at,
        )


def _context_for(transcription: str, overrides: ContextOverrides | None) -> ContextBlock:
    overrides = overrides or ContextOverrides()
    custom = any(
        value is not None
        for value in (overrides.format, overrides.detail_level, overrides.max_examples)
    )
    if not custom:
        return get_context_block(transcription)
    return build_context_block(
        fmt=overrides.format,
        detail_level=overrides.detail_level,
        max_examples=overrides.max_examples,
    )


def _add_usage(left: UsageInfo, right: llm_client.LLMResult) -> UsageInfo:
    return UsageInfo(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
        cached_tokens=left.cached_tokens + right.cached_tokens,
        cache_creation_tokens=(left.cache_creation_tokens or 0) + (right.cache_creation_tokens or 0) or None,
    )


def _usage_from(result: llm_client.LLMResult) -> UsageInfo:
    return UsageInfo(
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        total_tokens=result.total_tokens,
        cached_tokens=result.cached_tokens,
        cache_creation_tokens=result.cache_creation_tokens,
    )


def _with_spend(error: ServiceError, usage: UsageInfo, cost: float | None, latency: float) -> ServiceError:
    """Adjunta al error el gasto de las llamadas que sí respondieron."""
    error.spent_usage = usage  # type: ignore[attr-defined]
    error.spent_cost = cost  # type: ignore[attr-defined]
    error.spent_ms = latency  # type: ignore[attr-defined]
    return error


def _require_complete(
    result: llm_client.LLMResult,
    *,
    usage: UsageInfo | None = None,
    cost: float | None = None,
    latency: float = 0,
) -> None:
    if result.finish_reason == "length":
        error = LLMOutputTruncatedError()
        raise _with_spend(error, usage, cost, latency) if usage is not None else error


def _require_kind(turn, has_history: bool) -> None:
    if not has_history and turn.kind != "estimation":
        raise InvalidLLMOutputError(
            "La primera respuesta tiene que ser una estimación (kind=estimation)."
        )


def _scrub_history(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Tacha PII de mensajes de usuario ya guardados antes de reenviarlos al modelo."""
    cleaned: list[ChatMessage] = []
    for message in messages:
        if message.role != "user":
            cleaned.append(message)
            continue
        text, _entities, _engine = redact_pii(message.content)
        if text == message.content:
            cleaned.append(message)
        else:
            cleaned.append(message.model_copy(update={"content": text}))
    return cleaned


async def generate_estimation(
    transcription: str,
    conversation_history: list[ChatMessage] | None = None,
    *,
    overrides: ContextOverrides | None = None,
    session_id: str | None = None,
) -> TurnResult:
    settings = get_settings()
    overrides = _guard_dynamic(overrides or ContextOverrides())
    scrubbed = guard_input(transcription)
    model = overrides.model or choose_model(_routing_text(scrubbed, overrides), has_history=bool(conversation_history))
    provider = derive_provider(model)
    # Observabilidad: etiquetas de la traza. No viajan al modelo.
    tags = [
        "estimation",
        "conversation" if session_id else "single-turn",
        f"format:{overrides.format or settings.CONTEXT_FORMAT}",
        f"detail:{overrides.detail_level or settings.CONTEXT_DETAIL_LEVEL}",
        f"cache:{overrides.prompt_cache or settings.LLM_PROMPT_CACHE}",
    ]
    # Observabilidad: abre la traza raíz. Sin claves, el bloque solo ejecuta _generate.
    reviewed = _review_enabled(overrides)
    if reviewed:
        overrides = replace(overrides, defer_cache=True)
        tags.append("review:acb")
    with estimation_trace(
        session_id,
        tags,
        {"model": model, "provider": provider},
        transcription=scrubbed,
    ):
        research = await _research(scrubbed, conversation_history, overrides)
        if research.results:
            # Con resultados de herramientas el turno no se cachea: esos datos caducan.
            overrides = _with_tool_results(overrides, research)
        result = await _generate(
            scrubbed,
            conversation_history,
            overrides=overrides,
            model=model,
            provider=provider,
        )
        if reviewed and _needs_review(result):
            result = await _review(result, scrubbed, conversation_history, overrides, model, provider)
        result = _add_research(result, research)
        if reviewed:
            _store_final(result)
        return result


RESEARCH_INPUT_CHARS = 8_000


async def _research(scrubbed: str, history: list[ChatMessage] | None, overrides: ContextOverrides) -> ResearchOutcome:
    """Fase de herramientas del tier (si la política ofrece alguna). Si falla, se estima sin ella."""
    dynamic = overrides.dynamic or DynamicContext()
    # La misma fuente para decidir y para investigar: transcripción y adjuntos ya tachados.
    source = _routing_text(scrubbed, overrides)[:RESEARCH_INPUT_CHARS]
    tools = offered_tools(
        _tier_config(overrides),
        transcript=source,
        has_prior_turns=bool(history),
        has_attachments=bool(dynamic.attachments),
    )
    if not tools:
        return ResearchOutcome()
    with traced_span("research") as span:
        outcome = await run_research(tools=tools, transcript=source, memory=dynamic.memory)
        _annotate(span, output={"offered": tools, "used": [item.tool for item in outcome.results]})
    return outcome


def _with_tool_results(overrides: ContextOverrides, research: ResearchOutcome) -> ContextOverrides:
    tool_results = [item.as_context() for item in research.results]  # Ya pasaron su guardrail en research.py.
    dynamic = replace(overrides.dynamic or DynamicContext(), tool_results=tool_results)
    return replace(overrides, dynamic=dynamic, defer_cache=True)


def _add_research(result: TurnResult, research: ResearchOutcome) -> TurnResult:
    """La fase de herramientas suma su gasto y deja constancia de qué se usó."""
    if not research.calls:
        return result
    usage = result.usage
    for extra in research.usage:
        usage = _sum_usage(usage, extra)
    cost = result.cost_usd if research.cost_usd is None else (result.cost_usd or 0) + research.cost_usd
    tools = [
        {"tool": item.tool, "ok": item.ok, "ms": round(item.ms, 1), "error": item.error} for item in research.results
    ]
    return replace(result, usage=usage, cost_usd=cost, latency_ms=result.latency_ms + research.latency_ms, tools=tools)


def _review_enabled(overrides: ContextOverrides) -> bool:
    """D14: la revisión la declara el tier (app/tiers.py); ACB_ENABLED la apaga entera."""
    config = _tier_config(overrides)
    return bool(config and config.review == "acb" and get_settings().ACB_ENABLED)


def _needs_review(result: TurnResult) -> bool:
    """Solo estimaciones nuevas y estimables. Una respuesta (answer) o un acierto de caché no."""
    estimable = result.estimation is not None and getattr(result.estimation, "is_estimable", False)
    return result.kind in ESTIMATION_KINDS and estimable and not result.cache_hit and result.view is not None


async def _review(
    first: TurnResult,
    scrubbed: str,
    history: list[ChatMessage] | None,
    overrides: ContextOverrides,
    model: str,
    provider: str,
) -> TurnResult:
    memory = overrides.dynamic.memory if overrides.dynamic else None
    attachments = first.attachments_used

    async def actor(feedback: list[dict], call: int) -> TurnResult:
        dynamic = replace(overrides.dynamic or DynamicContext(), critic_feedback=feedback)
        retry = replace(overrides, dynamic=dynamic, call_name=f"actor-{call}")
        return await _generate(scrubbed, history, overrides=retry, model=model, provider=provider)

    def subject_of(result: TurnResult) -> Subject:
        return Subject(tier=result.tier or "", view=result.view, estimation=result.estimation, memory=memory)

    outcome = await run_review(
        first,
        actor=actor,
        subject_of=subject_of,
        transcript=scrubbed,
        attachments=attachments,
        conversation=_conversation_excerpt(history),
        max_actor_calls=get_settings().BOSS_MAX_ITERATIONS,
    )
    chosen: TurnResult = outcome.result  # type: ignore[assignment]
    usage = first.usage
    for extra in outcome.extra_usage:
        usage = _sum_usage(usage, extra)
    cost = None if first.cost_usd is None and outcome.extra_cost is None else (first.cost_usd or 0) + (outcome.extra_cost or 0)
    warnings = [*chosen.warnings, *_review_warnings(outcome.trace)]
    # La traza raíz muestra la salida que eligió el Boss, no la del último candidato.
    set_trace_io(
        input_text=scrubbed,
        output_text=chosen.estimation_markdown or (chosen.message or ""),
        metadata={
            "review_decision": outcome.trace.decision,
            "actor_calls": outcome.trace.actor_calls,
            "open_issues": len(outcome.trace.open_issues),
            "cost_usd": None if cost is None else round(cost, 6),
        },
    )
    return replace(
        chosen,
        usage=usage,
        cost_usd=cost,
        latency_ms=first.latency_ms + outcome.extra_ms,
        warnings=warnings,
        review=outcome.trace,
        # La clave es la del turno original: un actor posterior lleva el feedback en su clave.
        cache_key=first.cache_key,
    )


CRITIC_CONTEXT_CHARS = 6_000


USER_TURN_CHARS = 1_200
ASSISTANT_TURN_CHARS = 500


def _assistant_gist(content: str) -> str:
    """De una estimación previa (JSON), lo que el crítico necesita: tipo, mensaje, título y resumen."""
    try:
        payload = json.loads(content)
    except (ValueError, TypeError):
        return content[:ASSISTANT_TURN_CHARS]
    estimation = payload.get("estimation") or {}
    parts = [payload.get("kind") or "", payload.get("message") or "", estimation.get("project_title") or "", estimation.get("summary") or ""]
    return " · ".join(part for part in parts if part)[:ASSISTANT_TURN_CHARS]


def _conversation_excerpt(history: list[ChatMessage] | None) -> str:
    """Contexto previo para el crítico, por rol: las peticiones del usuario completas (acotadas)
    y de cada estimación anterior solo su resumen, para que un JSON largo no las desplace."""
    if not history:
        return ""
    lines: list[str] = []
    for message in history:
        if message.role == "user":
            lines.append(f"[usuario] {message.content[:USER_TURN_CHARS]}")
        else:
            lines.append(f"[asistente] {_assistant_gist(message.content)}")
    text = "\n".join(lines)
    # Si aun así no cabe, se quedan los turnos más recientes enteros.
    while len(text) > CRITIC_CONTEXT_CHARS and len(lines) > 1:
        lines.pop(0)
        text = "\n".join(lines)
    return text[-CRITIC_CONTEXT_CHARS:]


def _review_warnings(trace: ReviewTrace) -> list[ValidationIssue]:
    """Lo que el Boss entrega abierto queda a la vista del usuario como aviso estructurado."""
    warnings = [
        ValidationIssue(
            code=f"review_{issue.category.replace(':', '_')}",
            severity="warning" if issue.blocking else "info",
            message=issue.description,
            field=issue.field_path,
        )
        for issue in trace.open_issues
    ]
    if any(not iteration.critic_ok for iteration in trace.iterations):
        warnings.append(
            ValidationIssue(
                code="critic_unavailable",
                severity="info",
                message="El auditor no respondió; solo se aplicaron las comprobaciones deterministas.",
            )
        )
    return warnings


def _sum_usage(left: UsageInfo, right: UsageInfo) -> UsageInfo:
    created = (left.cache_creation_tokens or 0) + (right.cache_creation_tokens or 0)
    return UsageInfo(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
        cached_tokens=left.cached_tokens + right.cached_tokens,
        cache_creation_tokens=created or None,
    )


def _store_final(result: TurnResult) -> None:
    """Con revisión, la caché exacta guarda la salida final bajo la clave del turno original.

    Solo si se aceptó con el auditor disponible: una síntesis con issues abiertos o una
    revisión incompleta no se sirve desde caché, porque sus avisos no viajan con ella.
    """
    estimable = result.estimation is not None and getattr(result.estimation, "is_estimable", False)
    if result.cache_hit or not estimable or result.cache_key is None or result.tools:
        return
    review = result.review
    if review is not None and (
        review.decision != "accept" or review.open_issues or not all(item.critic_ok for item in review.iterations)
    ):
        return
    store(result.cache_key, content=result.history_content, model=result.model, finish_reason=result.finish_reason)


@dataclass
class _Prepared:
    block: ContextBlock
    history: list[ChatMessage]
    dropped: int
    messages: list[dict]
    budget: object
    version: str
    response_format: dict
    metadata: dict
    key: str
    cached: object
    form_detail_level: object = DetailLevel.MEDIUM
    output_format: object = OutputFormat.PHASES_TABLE
    project_type: object | None = None
    prompt_label: str = DEFAULT_PROMPT_VERSION
    cache_kind: str | None = None
    cache_similarity: float | None = None
    semantic_vector: list[float] | None = None
    semantic_bucket: str | None = None
    scrubbed_text: str = ""
    # Hubo turnos antes, aunque el recorte los haya dejado fuera del prompt.
    has_prior_turns: bool = False
    # Hay contexto dinámico (memoria, adjuntos, herramientas): la caché semántica no aplica.
    has_dynamic_context: bool = False
    tier: str | None = None
    memory: dict | None = None  # Memoria de la sesión, para las propiedades online.
    attachments: list[dict] = field(default_factory=list)
    defer_cache: bool = False


def _routing_text(scrubbed: str, overrides: ContextOverrides) -> str:
    """Lo que mide la complejidad para elegir modelo: la transcripción y sus adjuntos."""
    attachments = overrides.dynamic.attachments if overrides.dynamic else []
    return "\n".join([scrubbed, *(item.get("text", "") for item in attachments)])


def _guard_dynamic(overrides: ContextOverrides) -> ContextOverrides:
    """Adjuntos y resultados de herramientas pasan su guardrail (PII tachada, inyección registrada)."""
    dynamic = overrides.dynamic
    if dynamic is None or not (dynamic.attachments or dynamic.tool_results):
        return overrides
    attachments = [
        {**item, "text": guard_dynamic_text(item.get("text", ""), source="attachment")} for item in dynamic.attachments
    ]
    tool_results = [
        {**item, "content": guard_dynamic_text(item.get("content", ""), source=f"tool:{item.get('tool')}")}
        for item in dynamic.tool_results
    ]
    guarded = replace(dynamic, attachments=attachments, tool_results=tool_results)
    return replace(overrides, dynamic=guarded)


def _redact_text(text: str) -> str:
    return redact_pii(text)[0]


def _tier_config(overrides: ContextOverrides) -> TierConfig | None:
    return resolve_tier_config(overrides.tier) if overrides.tier else None


def _system_template(overrides: ContextOverrides) -> str:
    config = _tier_config(overrides)
    return config.template if config else "system.j2"


def _dynamic_payload(dynamic: DynamicContext | None, legacy_metadata: str = "") -> dict | None:
    """El contexto dinámico que afecta a la respuesta, como dato serializable para las cachés.

    ``legacy_metadata`` es el bloque JSON de v3, que tampoco entra en el hash de versión.
    """
    dynamic = dynamic or DynamicContext()
    payload = {
        "memory": dynamic.memory,
        "attachments": dynamic.attachments,
        "tool_results": dynamic.tool_results,
        "critic_feedback": dynamic.critic_feedback,
        "legacy_metadata": legacy_metadata or None,
    }
    return payload if any(payload.values()) else None


def _field_value(field: object | None) -> str | None:
    if field is None:
        return None
    return field.value if hasattr(field, "value") else str(field)


def _log_cache_hit(cached, estimated_input_tokens: int) -> None:
    """Sesión 3 · logs: un acierto no es una llamada, pero deja las dos líneas."""
    provider = derive_provider(cached.model)
    log_llm_started(
        model=cached.model,
        provider=provider,
        tokens_in=estimated_input_tokens,
        cache_hit=True,
    )
    log_llm_completed(
        model=cached.model,
        provider=provider,
        tokens_in=0,
        tokens_out=0,
        latency_ms=0,
        cost_usd=0,
        finish_reason=cached.finish_reason,
        fallback_used=False,
        cache_hit=True,
    )


def _cached_result(cached) -> llm_client.LLMResult:
    return llm_client.LLMResult(
        content=cached.content,
        model=cached.model,
        finish_reason=cached.finish_reason,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        cached_tokens=0,
        cache_creation_tokens=None,
        cost_usd=0,
        latency_ms=0,
    )


def _prepare(
    transcription: str,
    conversation_history: list[ChatMessage] | None,
    *,
    overrides: ContextOverrides,
    model: str,
    provider: str,
) -> _Prepared:
    settings = get_settings()
    # El caller ya filtró la entrada actual; se vuelve a aplicar por si llega cruda.
    transcription = guard_input(transcription)
    history_source = _scrub_history(list(conversation_history or []))
    # Observabilidad: el span anota el paso. _context_for es el trabajo del estimador.
    with traced_span("build-context") as context_span:
        block = _context_for(transcription, overrides)
        _annotate(
            context_span,
            output={
                "examples": block.included_ids,
                "excluded": [item.id for item in block.excluded],
                "detail_level": block.detail_level,
                "format": block.format,
            },
        )

    history, dropped = _history_that_fits(transcription, block, history_source, overrides, model)
    prompt_label = overrides.prompt_version or settings.LLM_PROMPT_VERSION or DEFAULT_PROMPT_VERSION
    form_detail = overrides.form_detail_level or DetailLevel.MEDIUM
    output_format = overrides.output_format or OutputFormat.PHASES_TABLE
    # Observabilidad: el span anota el prompt. El 413 lo decide el presupuesto, no la traza.
    with traced_span("build-prompt") as prompt_span:
        try:
            messages = apply_cache_strategy(
                build_messages(
                    transcription,
                    block,
                    history,
                    prompt_label=prompt_label,
                    detail_level=form_detail,  # type: ignore[arg-type]
                    output_format=output_format,  # type: ignore[arg-type]
                    project_type=overrides.project_type,
                    project_metadata=overrides.project_metadata,
                    dynamic=overrides.dynamic,
                    system_template=_system_template(overrides),
                ),
                provider,
                block,
                overrides.prompt_cache,
            )
        except UnknownPromptVersionError as exc:
            raise PromptVersionError(str(exc)) from exc
        budget = _budget(messages, block, transcription, history, model, overrides.dynamic)
        if not budget.fits:
            raise ContextBudgetExceededError(
                "La petición no cabe en la ventana de contexto con el margen de seguridad. "
                f"Entrada aproximada: {budget.total_input} tokens; ventana: {budget.window}."
            )
        tier_config = _tier_config(overrides)
        # Con tier, la versión dice también qué plantilla de perfil se usó (v4-pm-<hash>).
        version_label = f"{prompt_label}-{tier_config.tier.value}" if tier_config else prompt_label
        version = prompt_version(
            system_text_of(messages) if block.examples_are_static else cacheable_text(messages, block),
            version_label,
        )
        _annotate(prompt_span, output={"prompt_version": version, "messages": len(messages)})

    # Sesión 3 · caché: la clave incluye el historial y las opciones del formulario.
    key = cache_key(
        transcription=transcription,
        model=model,
        temperature=settings.LLM_TEMPERATURE,
        prompt_version=version,
        history=history,
        project_type=_field_value(overrides.project_type),
        form_detail_level=_field_value(form_detail),
        output_format=_field_value(output_format),
        dynamic=_dynamic_payload(overrides.dynamic, overrides.project_metadata),
    )
    cached = lookup(key)
    return _Prepared(
        block=block,
        history=history,
        dropped=dropped,
        messages=messages,
        budget=budget,
        version=version,
        response_format=(
            build_response_format(tier_config.turn_schema, f"{tier_config.tier.value}_turn")
            if tier_config
            else build_response_format()
        ),
        tier=tier_config.tier.value if tier_config else None,
        memory=overrides.dynamic.memory if overrides.dynamic else None,
        attachments=list(overrides.dynamic.attachments) if overrides.dynamic else [],
        defer_cache=overrides.defer_cache,
        metadata={"prompt_version": version, "examples": ",".join(block.included_ids)},
        key=key,
        cached=cached,
        form_detail_level=form_detail,
        output_format=output_format,
        project_type=overrides.project_type,
        prompt_label=prompt_label,
        cache_kind="exact" if cached is not None else None,
        scrubbed_text=transcription,
        has_prior_turns=bool(history_source),
        has_dynamic_context=_dynamic_payload(overrides.dynamic, overrides.project_metadata) is not None,
    )


async def _apply_semantic_cache(prepared: _Prepared, *, model: str) -> _Prepared:
    """Tras la caché exacta: mide o sirve un vecino semántico. Solo el primer turno."""
    settings = get_settings()
    mode = settings.SEMANTIC_CACHE_MODE
    # Con revisión (defer_cache) tampoco: un vecino semántico se serviría sin pasar por el crítico.
    skip = prepared.has_prior_turns or prepared.has_dynamic_context or prepared.defer_cache
    if mode == "off" or skip or prepared.cached is not None:
        return prepared
    try:
        vector = await llm_client.embed(prepared.scrubbed_text)
    except Exception as exc:  # noqa: BLE001
        log_event("semantic_cache_embed_failed", detail=str(exc))
        return prepared
    bucket = bucket_id(
        prompt_version=prepared.version,
        project_type=prepared.project_type.value if hasattr(prepared.project_type, "value") else prepared.project_type,
        detail_level=prepared.form_detail_level.value
        if hasattr(prepared.form_detail_level, "value")
        else prepared.form_detail_level,
        output_format=prepared.output_format.value
        if hasattr(prepared.output_format, "value")
        else prepared.output_format,
        model=model,
    )
    prepared.semantic_vector = vector
    prepared.semantic_bucket = bucket
    hit = lookup_semantic(bucket=bucket, vector=vector)
    would_hit = hit is not None and hit.similarity >= settings.SEMANTIC_CACHE_THRESHOLD
    emit_semantic_check(bucket=bucket, hit=hit, would_hit=would_hit, mode=mode)
    if mode == "on" and would_hit and hit is not None and hit.content:
        prepared.cached = CachedCompletion(
            content=hit.content,
            model=hit.model or model,
            finish_reason=hit.finish_reason,
            expires_at=time.monotonic() + 60,
        )
        prepared.cache_kind = "semantic"
        prepared.cache_similarity = hit.similarity
    return prepared


class EstimationStream:
    """Sesión 3 · streaming: un acierto sale entero. Si no, cada entrega es el markdown visible.

    El modelo sigue mandando JSON. Aquí se traduce antes de salir. Al terminar,
    `result` es la estimación ya validada.
    """

    def __init__(
        self,
        transcription: str,
        conversation_history: list[ChatMessage] | None = None,
        *,
        overrides: ContextOverrides | None = None,
        session_id: str | None = None,
    ) -> None:
        self.transcription = transcription
        self.conversation_history = conversation_history
        self.overrides = overrides or ContextOverrides()
        self.session_id = session_id
        self.result: TurnResult | None = None
        self.from_cache = False

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        settings = get_settings()
        overrides = self.overrides = _guard_dynamic(self.overrides)
        scrubbed = guard_input(self.transcription)
        self.transcription = scrubbed
        model = overrides.model or choose_model(
            _routing_text(scrubbed, overrides), has_history=bool(self.conversation_history)
        )
        provider = derive_provider(model)
        tags = [
            "estimation",
            "conversation" if self.session_id else "single-turn",
            f"format:{overrides.format or settings.CONTEXT_FORMAT}",
            f"detail:{overrides.detail_level or settings.CONTEXT_DETAIL_LEVEL}",
            f"cache:{overrides.prompt_cache or settings.LLM_PROMPT_CACHE}",
        ]
        with estimation_trace(
            self.session_id,
            tags,
            {"model": model, "provider": provider},
            transcription=scrubbed,
        ):
            prepared = _prepare(
                scrubbed,
                self.conversation_history,
                overrides=overrides,
                model=model,
                provider=provider,
            )
            prepared = await _apply_semantic_cache(prepared, model=model)
            if prepared.cached is not None:
                self.from_cache = True
                _log_cache_hit(prepared.cached, prepared.budget.total_input)
                first = _cached_result(prepared.cached)
                self.result = await _assemble(first, prepared, transcription=scrubbed, model=model)
                yield self.result.estimation_markdown or self.result.message or ""
            else:
                tokens = llm_client.TokenStream(
                    prepared.messages,
                    prepared.response_format,
                    name="generate-estimate",
                    metadata=prepared.metadata,
                    model=model,
                    estimated_input_tokens=prepared.budget.total_input,
                )
                raw = ""
                shown = ""
                async for piece in tokens:
                    raw += piece
                    preview = preview_markdown(raw)
                    if preview and preview != shown:
                        # Mismos filtros de PII que la salida final, antes de pintar el borrador.
                        preview_safe, _entities, _engine = redact_pii(preview)
                        yield preview_safe
                        shown = preview
                if tokens.result is None:
                    raise LLMUnavailableError("El modelo no envió una respuesta.")
                first = tokens.result
                self.result = await _assemble(first, prepared, transcription=scrubbed, model=model)


def stream_estimation(
    transcription: str,
    conversation_history: list[ChatMessage] | None = None,
    *,
    overrides: ContextOverrides | None = None,
    session_id: str | None = None,
) -> EstimationStream:
    """Sesión 3 · streaming: lo que el front y el SSE iteran. El POST sigue en generate_estimation."""
    return EstimationStream(
        transcription,
        conversation_history,
        overrides=overrides,
        session_id=session_id,
    )


async def _generate(
    transcription: str,
    conversation_history: list[ChatMessage] | None,
    *,
    overrides: ContextOverrides,
    model: str,
    provider: str,
) -> TurnResult:
    prepared = _prepare(
        transcription,
        conversation_history,
        overrides=overrides,
        model=model,
        provider=provider,
    )
    prepared = await _apply_semantic_cache(prepared, model=model)
    if prepared.cached is None:
        first = await llm_client.complete(
            prepared.messages,
            prepared.response_format,
            name=overrides.call_name,
            metadata=prepared.metadata,
            model=model,
            estimated_input_tokens=prepared.budget.total_input,
        )
    else:
        _log_cache_hit(prepared.cached, prepared.budget.total_input)
        first = _cached_result(prepared.cached)
    return await _assemble(first, prepared, transcription=transcription, model=model)


async def _assemble(first: llm_client.LLMResult, prepared: _Prepared, *, transcription: str, model: str) -> TurnResult:
    settings = get_settings()
    messages = prepared.messages
    response_format = prepared.response_format
    metadata = prepared.metadata
    version = prepared.version
    block = prepared.block
    budget = prepared.budget
    cached = prepared.cached
    key = prepared.key
    dropped = prepared.dropped
    repairs = 0
    answered = first
    usage = _usage_from(first)
    cost = first.cost_usd
    latency = first.latency_ms
    _require_complete(first, usage=usage, cost=cost, latency=latency)
    parse = (lambda content: parse_tier_turn(content, prepared.tier)) if prepared.tier else parse_turn
    try:
        turn = parse(first.content)
        _require_kind(turn, prepared.has_prior_turns)
    except InvalidLLMOutputError as exc:
        repairs = 1
        repair_messages = ensure_alternation(
            [
                *messages,
                {"role": "assistant", "content": first.content},
                {"role": "user", "content": repair_instruction(exc.message, prepared.prompt_label)},
            ]
        )
        second = await llm_client.complete(
            repair_messages,
            response_format,
            name="repair-estimate",
            metadata=metadata,
            model=model,
        )
        usage = _add_usage(usage, second)
        latency += second.latency_ms
        if second.cost_usd is not None:
            cost = (cost or 0) + second.cost_usd
        _require_complete(second, usage=usage, cost=cost, latency=latency)
        try:
            turn = parse(second.content)
            _require_kind(turn, prepared.has_prior_turns)
        except InvalidLLMOutputError as failed:
            # El fallo lleva lo que costó: quien orquesta (la revisión) no debe perder esas llamadas.
            raise _with_spend(failed, usage, cost, latency) from exc
        answered = second

    if turn.message:
        scrubbed_message, _entities, _engine = redact_pii(turn.message)
        if scrubbed_message != turn.message:
            turn = turn.model_copy(update={"message": scrubbed_message})

    estimation = None
    view = None
    warnings: list[ValidationIssue] = []
    markdown = None
    if turn.kind in {"estimation", "estimation_update"}:
        # Observabilidad: el span anota los avisos. validate_and_fix es el trabajo del estimador.
        with traced_span("postprocess") as post_span:
            # Con tier, el desglose común del borrador pasa por el mismo motor de siempre.
            draft = to_estimation_draft(turn.estimation) if prepared.tier else turn.estimation
            estimation, warnings = validate_and_fix(
                draft,  # type: ignore[arg-type]
                require_phase_assumptions=(not prepared.tier) and prepared.form_detail_level == DetailLevel.DETAILED,
            )
            estimation, guard_warnings = guard_output(estimation)
            warnings = [*warnings, *guard_warnings]
            if prepared.tier:
                # El borrador corregido y tachado es la única fuente: vista, historial y cachés.
                safe_draft = redact_model(corrected_draft(turn.estimation, estimation), _redact_text)  # type: ignore[arg-type]
                view = redact_model(build_view(prepared.tier, safe_draft, estimation), _redact_text)
                markdown = render_view_markdown(view)
            else:
                markdown = render_markdown(estimation)
            _annotate(post_span, output={"warnings": [issue.code for issue in warnings]})

    if prepared.tier:
        history_content = tier_history_content(turn, safe_draft if estimation is not None else None)
    else:
        history_content = to_history_content(turn, estimation)

    # Exacta y semántica solo tras validar y pasar guardrails; solo si es estimable.
    # Se guarda el turno ya postprocesado (mismo JSON que el historial), no el bruto del modelo.
    if cached is None and estimation is not None and estimation.is_estimable and not prepared.defer_cache:
        store(key, content=history_content, model=answered.model, finish_reason=answered.finish_reason)
        if prepared.semantic_vector is not None and prepared.semantic_bucket is not None and not prepared.has_prior_turns:
            store_semantic(
                bucket=prepared.semantic_bucket,
                vector=prepared.semantic_vector,
                prompt=prepared.scrubbed_text,
                content=history_content,
                model=answered.model,
                finish_reason=answered.finish_reason,
            )

    result = TurnResult(
        kind=turn.kind,
        message=turn.message,
        estimation_markdown=markdown,
        estimation=estimation,
        warnings=warnings,
        history_content=history_content,
        prompt_version=version,
        context=ContextMeta(
            format=block.format,
            detail_level=block.detail_level,
            examples=block.included_ids,
            prompt_version=version,
        ),
        token_budget=budget,
        usage=usage,
        cost_usd=cost,
        latency_ms=latency,
        # Sesión 3 · fallback: el modelo publicado es el que contestó.
        model=answered.model,
        provider=derive_provider(answered.model),
        trace_id=current_trace_id(),  # Observabilidad: None si Langfuse está apagado.
        cache_hit=cached is not None,
        cache_kind=prepared.cache_kind if cached is not None else None,
        cache_similarity=prepared.cache_similarity if cached is not None else None,
        finish_reason=answered.finish_reason,
        dropped_turns=dropped,
        repairs=repairs,
        scrubbed_input=transcription,
        tier=prepared.tier,
        view=view,
        attachments_used=prepared.attachments,
        cache_key=key,
    )
    # Observabilidad: la estimación ya está en result. Esto solo puntúa y registra.
    scores = collect_metrics(
        first_try=repairs == 0,
        warnings=warnings,
        estimation=estimation,
        usage=usage,
        cost_usd=cost,
        latency_ms=latency,
        view=view,
        tier=prepared.tier,
        memory=prepared.memory,
    )
    record_scores(scores)
    set_trace_io(
        input_text=transcription,
        output_text=markdown or (turn.message or ""),
        metadata={
            "model": answered.model,
            "provider": derive_provider(answered.model),
            "prompt_version": version,
            "kind": turn.kind,
            "repairs": repairs,
            "cost_usd": None if cost is None else round(cost, 6),
        },
    )
    _log(result, settings.LOG_PROMPTS, transcription, messages)  # Log local, no es una traza.
    tags = [f"prompt:{version}", f"format:{block.format}", f"detail:{block.detail_level}"]
    del tags
    return result


def _annotate(observation, **fields) -> None:
    """Observabilidad: adjunta datos al span. Sin Langfuse, observation es None."""
    if observation is not None:
        observation.update(**fields)


def _history_that_fits(transcription, block, history, overrides, model):
    if not history:
        return [], 0
    settings = get_settings()
    prompt_label = overrides.prompt_version or settings.LLM_PROMPT_VERSION or DEFAULT_PROMPT_VERSION
    form_detail = overrides.form_detail_level or DetailLevel.MEDIUM
    output_format = overrides.output_format or OutputFormat.PHASES_TABLE
    fixed = _budget(
        build_messages(
            transcription,
            block,
            prompt_label=prompt_label,
            detail_level=form_detail,  # type: ignore[arg-type]
            output_format=output_format,  # type: ignore[arg-type]
            project_type=overrides.project_type,
            project_metadata=overrides.project_metadata,
            dynamic=overrides.dynamic,
            system_template=_system_template(overrides),
        ),
        block,
        transcription,
        [],
        model,
        overrides.dynamic,
    )
    room = (
        settings.MODEL_CONTEXT_WINDOW
        - settings.CONTEXT_SAFETY_MARGIN_TOKENS
        - settings.LLM_MAX_TOKENS
        - fixed.total_input
    )
    if overrides.session_window:
        return slide_history(history, room_tokens=max(room, 0))
    return select_history(history, max_turns=settings.CONVERSATION_MAX_TURNS, room_tokens=max(room, 0))


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(part.get("text", "") for part in content if isinstance(part, dict))


def _budget(
    messages,
    block: ContextBlock,
    transcription: str,
    history: list[ChatMessage],
    model: str,
    dynamic: DynamicContext | None = None,
):
    """Presupuesto por bloque. Cada fuente de contexto dinámico tiene el suyo (regla 2 de la fase 1)."""
    settings = get_settings()
    system = system_text_of(messages)
    # Todo el system salvo los ejemplos, que van aparte: incluye scope, constraints y metadatos.
    before, _, rest = system.partition("<examples>")
    _examples, _, after = rest.partition("</examples>")
    instructions = f"{before}{after}"
    history_text = "\n".join(message.content for message in history)
    dynamic = dynamic or DynamicContext()
    extra = {
        "attachments": "\n".join(item.get("text", "") for item in dynamic.attachments),
        "tool_results": "\n".join(item.get("content", "") for item in dynamic.tool_results),
        "critic_feedback": "\n".join(item.get("description", "") for item in dynamic.critic_feedback),
    }
    # Lo que envuelve el turno (recordatorio, etiquetas) para que el total sea el mensaje real.
    current = _text_of(messages[-1]["content"]) if len(messages) > 1 else transcription
    framing = max(0, count_tokens(current) - count_tokens(transcription) - sum(count_tokens(t) for t in extra.values()))
    return build_token_budget(
        instructions=instructions,
        context=block.examples_text,
        transcription=transcription,
        extra_blocks={name: text for name, text in extra.items() if text},
        framing_tokens=framing,
        history=history_text,
        reserved_output=settings.LLM_MAX_TOKENS,
        safety_margin=settings.CONTEXT_SAFETY_MARGIN_TOKENS,
        window=settings.MODEL_CONTEXT_WINDOW,
        model=model,
        cacheable=cacheable_text(messages, block),
    )


def _log(result: TurnResult, log_prompts: bool, transcription: str, messages: list[dict]) -> None:
    """Observabilidad local: una línea JSON en stdout. Independiente de Langfuse."""
    fields = {
        "model": result.model,
        "provider": result.provider,
        "prompt_version": result.prompt_version,
        "kind": result.kind,
        "format": result.context.format,
        "detail_level": result.context.detail_level,
        "examples": result.context.examples,
        "input_tokens": result.usage.input_tokens,
        "output_tokens": result.usage.output_tokens,
        "cached_tokens": result.usage.cached_tokens,
        "cost_usd": result.cost_usd,
        "latency_ms": round(result.latency_ms, 2),
        "cache_hit": result.cache_hit,
        "repairs": result.repairs,
        "warnings": [issue.code for issue in result.warnings],
        "dropped_turns": result.dropped_turns,
        "trace_id": result.trace_id,
    }
    if log_prompts:
        fields["transcription"] = transcription
        fields["messages"] = messages
    log_event("estimation", **fields)


def get_context_info(include_text: bool = False) -> ContextInfo:
    settings = get_settings()
    block = get_context_block("")
    provider = derive_provider(settings.LLM_MODEL)
    sample = "Transcripción de ejemplo para medir el prefijo del prompt, sin llamarlo."
    messages = apply_cache_strategy(build_messages(sample, block), provider, block)
    budget = _budget(messages, block, "", [], settings.LLM_MODEL)
    prefix_tokens = budget.cacheable_tokens
    strategy = "off"
    if settings.LLM_PROMPT_CACHE == "auto":
        strategy = "explicit" if provider == "anthropic" else "implicit"
    return ContextInfo(
        examples_included=block.included_ids,
        examples_excluded=block.excluded,
        detail_level=block.detail_level,
        format=block.format,
        notes=block.notes,
        token_budget=budget,
        prompt_cache_mode=settings.LLM_PROMPT_CACHE,
        prompt_cache_strategy=strategy,
        prefix_approx_tokens=prefix_tokens,
        likely_cacheable=prefix_tokens >= CACHE_MIN_TOKENS,
        text=block.text if include_text else None,
    )


def preview_prompt(
    transcription: str,
    *,
    prompt_label: str = "v1",
    detail_level=None,
    output_format=None,
    project_type=None,
):
    kwargs = {"prompt_label": prompt_label}
    if detail_level is not None:
        kwargs["detail_level"] = detail_level
    if output_format is not None:
        kwargs["output_format"] = output_format
    if project_type is not None:
        kwargs["project_type"] = project_type
    return build_preview(transcription, **kwargs)
