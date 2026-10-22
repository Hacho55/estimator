"""Caso de uso: una transcripción (y, si viene, un historial) entra, una estimación sale.

No conoce HTTP ni conversaciones. El historial es una lista de mensajes que el
router decide pasar. Sin historial, la llamada es de un solo turno.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.config import derive_provider, get_settings
from app.context.preprocessing import build_context_block, get_context_block
from app.evaluation.metrics import collect_metrics
from app.observability import (
    current_trace_id,
    estimation_trace,
    log_event,
    record_scores,
    set_trace_io,
    traced_span,
)
from app.schemas.context import ContextBlock, ContextInfo
from app.schemas.conversation import ChatMessage
from app.schemas.estimation import (
    ContextMeta,
    EstimationResponse,
    UsageInfo,
    ValidationIssue,
)
from app.services import llm_client
from app.services.errors import (
    ContextBudgetExceededError,
    InvalidLLMOutputError,
    LLMOutputTruncatedError,
)
from app.services.postprocessor import (
    parse_turn,
    render_markdown,
    to_history_content,
    validate_and_fix,
)
from app.services.prompt_builder import (
    REPAIR_INSTRUCTION,
    apply_cache_strategy,
    build_messages,
    build_preview,
    build_response_format,
    cacheable_text,
    ensure_alternation,
    prompt_version,
    system_text_of,
)
from app.state.conversations import select_history
from app.tokens import CACHE_MIN_TOKENS, build_token_budget

GATEWAY = "litellm"


@dataclass
class ContextOverrides:
    format: str | None = None
    detail_level: str | None = None
    max_examples: int | None = None
    model: str | None = None
    prompt_cache: str | None = None


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
    dropped_turns: int = 0
    repairs: int = 0
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


def _require_complete(result: llm_client.LLMResult) -> None:
    if result.finish_reason == "length":
        raise LLMOutputTruncatedError()


def _require_kind(turn, has_history: bool) -> None:
    if not has_history and turn.kind != "estimation":
        raise InvalidLLMOutputError(
            "La primera respuesta tiene que ser una estimación (kind=estimation)."
        )


async def generate_estimation(
    transcription: str,
    conversation_history: list[ChatMessage] | None = None,
    *,
    overrides: ContextOverrides | None = None,
    session_id: str | None = None,
) -> TurnResult:
    settings = get_settings()
    overrides = overrides or ContextOverrides()
    model = overrides.model or settings.LLM_MODEL
    provider = derive_provider(model)
    tags = [
        f"format:{overrides.format or settings.CONTEXT_FORMAT}",
        f"detail:{overrides.detail_level or settings.CONTEXT_DETAIL_LEVEL}",
        f"cache:{overrides.prompt_cache or settings.LLM_PROMPT_CACHE}",
    ]
    with estimation_trace(session_id, tags, {"model": model, "provider": provider}):
        return await _generate(
            transcription,
            conversation_history,
            overrides=overrides,
            model=model,
            provider=provider,
            session_id=session_id,
        )


async def _generate(
    transcription: str,
    conversation_history: list[ChatMessage] | None,
    *,
    overrides: ContextOverrides,
    model: str,
    provider: str,
    session_id: str | None,
) -> TurnResult:
    settings = get_settings()
    with traced_span("build-context"):
        block = _context_for(transcription, overrides)

    history, dropped = _history_that_fits(transcription, block, conversation_history or [], overrides, model)
    with traced_span("build-prompt"):
        messages = apply_cache_strategy(
            build_messages(transcription, block, history),
            provider,
            block,
            overrides.prompt_cache,
        )
        budget = _budget(messages, block, transcription, history, model)
        if not budget.fits:
            raise ContextBudgetExceededError(
                "La petición no cabe en la ventana de contexto con el margen de seguridad. "
                f"Entrada aproximada: {budget.total_input} tokens; ventana: {budget.window}."
            )
        version = prompt_version(system_text_of(messages) if block.examples_are_static else cacheable_text(messages, block))

    response_format = build_response_format()
    metadata = {
        "prompt_version": version,
        "examples": block.included_ids,
        "session_id": session_id,
    }
    with traced_span("estimation-turn", as_type="generation"):
        first = await llm_client.complete(
            messages, response_format, name="estimation-turn", metadata=metadata, model=model
        )
    _require_complete(first)
    repairs = 0
    usage = _usage_from(first)
    cost = first.cost_usd
    latency = first.latency_ms
    try:
        turn = parse_turn(first.content)
        _require_kind(turn, bool(history))
    except InvalidLLMOutputError as exc:
        repairs = 1
        repair_messages = ensure_alternation(
            [
                *messages,
                {"role": "assistant", "content": first.content},
                {"role": "user", "content": REPAIR_INSTRUCTION.format(error=exc.message)},
            ]
        )
        with traced_span("estimation-repair", as_type="generation"):
            second = await llm_client.complete(
                repair_messages,
                response_format,
                name="estimation-repair",
                metadata=metadata,
                model=model,
            )
        _require_complete(second)
        turn = parse_turn(second.content)
        _require_kind(turn, bool(history))
        usage = _add_usage(usage, second)
        latency += second.latency_ms
        if second.cost_usd is not None:
            cost = (cost or 0) + second.cost_usd

    estimation = None
    warnings: list[ValidationIssue] = []
    markdown = None
    if turn.kind in {"estimation", "estimation_update"}:
        with traced_span("postprocess"):
            estimation, warnings = validate_and_fix(turn.estimation)  # type: ignore[arg-type]
            markdown = render_markdown(estimation)

    result = TurnResult(
        kind=turn.kind,
        message=turn.message,
        estimation_markdown=markdown,
        estimation=estimation,
        warnings=warnings,
        history_content=to_history_content(turn, estimation),
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
        model=first.model,
        provider=provider,
        trace_id=current_trace_id(),
        dropped_turns=dropped,
        repairs=repairs,
    )
    scores = collect_metrics(
        first_try=repairs == 0,
        warnings=warnings,
        estimation=estimation,
        usage=usage,
        cost_usd=cost,
        latency_ms=latency,
    )
    record_scores(scores)
    set_trace_io(
        input_text=transcription,
        output_text=markdown or (turn.message or ""),
        metadata={"prompt_version": version, "kind": turn.kind, "repairs": repairs},
    )
    _log(result, settings.LOG_PROMPTS, transcription, messages)
    tags = [f"prompt:{version}", f"format:{block.format}", f"detail:{block.detail_level}"]
    del tags
    return result


def _history_that_fits(transcription, block, history, overrides, model):
    if not history:
        return [], 0
    settings = get_settings()
    fixed = _budget(build_messages(transcription, block), block, transcription, [], model)
    room = (
        settings.MODEL_CONTEXT_WINDOW
        - settings.CONTEXT_SAFETY_MARGIN_TOKENS
        - settings.LLM_MAX_TOKENS
        - fixed.total_input
    )
    return select_history(history, max_turns=settings.CONVERSATION_MAX_TURNS, room_tokens=max(room, 0))


def _budget(messages, block: ContextBlock, transcription: str, history: list[ChatMessage], model: str):
    settings = get_settings()
    system = system_text_of(messages)
    instructions, _, _rest = system.partition("<ejemplos>")
    history_text = "\n".join(message.content for message in history)
    return build_token_budget(
        instructions=instructions,
        context=block.examples_text,
        transcription=transcription,
        history=history_text,
        reserved_output=settings.LLM_MAX_TOKENS,
        safety_margin=settings.CONTEXT_SAFETY_MARGIN_TOKENS,
        window=settings.MODEL_CONTEXT_WINDOW,
        model=model,
        cacheable=cacheable_text(messages, block),
    )


def _log(result: TurnResult, log_prompts: bool, transcription: str, messages: list[dict]) -> None:
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


def preview_prompt(transcription: str):
    return build_preview(transcription)
