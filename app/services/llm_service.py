"""Caso de uso: una transcripción (y, si viene, un historial) entra, una estimación sale.

No conoce HTTP ni conversaciones. El historial es una lista de mensajes que el
router decide pasar. Sin historial, la llamada es de un solo turno.

Observabilidad, mezclada aquí a propósito y marcada en cada sitio: estimation_trace,
traced_span, record_scores, set_trace_io y log_event. Ninguna cambia la estimación.
La implementación vive en app/observability.py.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.config import derive_provider, get_settings
from app.context.preprocessing import build_context_block, get_context_block
from app.evaluation.metrics import collect_metrics
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
from app.services import llm_client
from app.services.errors import (
    ContextBudgetExceededError,
    InvalidLLMOutputError,
    LLMOutputTruncatedError,
    LLMUnavailableError,
    PromptVersionError,
)
from app.services.guardrails import guard_input, guard_output, redact_pii
from app.services.postprocessor import (
    parse_turn,
    preview_markdown,
    render_markdown,
    to_history_content,
    validate_and_fix,
)
from app.services.prompt_builder import (
    DEFAULT_PROMPT_VERSION,
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
from app.state.conversations import select_history
from app.state.response_cache import CachedCompletion, cache_key, lookup, store
from app.state.semantic_cache import (
    bucket_id,
    emit_semantic_check,
    lookup_semantic,
    store_semantic,
)
from app.state.sessions import slide_history
from app.tokens import CACHE_MIN_TOKENS, build_token_budget

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
    project_metadata: str = ""
    session_window: bool = False  # Sesión: recorta por pares viejos, sin anclas.


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


def _require_complete(result: llm_client.LLMResult) -> None:
    if result.finish_reason == "length":
        raise LLMOutputTruncatedError()


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
    overrides = overrides or ContextOverrides()
    scrubbed = guard_input(transcription)
    model = overrides.model or choose_model(scrubbed, has_history=bool(conversation_history))
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
    with estimation_trace(
        session_id,
        tags,
        {"model": model, "provider": provider},
        transcription=scrubbed,
    ):
        return await _generate(
            scrubbed,
            conversation_history,
            overrides=overrides,
            model=model,
            provider=provider,
        )


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
                ),
                provider,
                block,
                overrides.prompt_cache,
            )
        except UnknownPromptVersionError as exc:
            raise PromptVersionError(str(exc)) from exc
        budget = _budget(messages, block, transcription, history, model)
        if not budget.fits:
            raise ContextBudgetExceededError(
                "La petición no cabe en la ventana de contexto con el margen de seguridad. "
                f"Entrada aproximada: {budget.total_input} tokens; ventana: {budget.window}."
            )
        version = prompt_version(
            system_text_of(messages) if block.examples_are_static else cacheable_text(messages, block),
            prompt_label,
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
    )
    cached = lookup(key)
    return _Prepared(
        block=block,
        history=history,
        dropped=dropped,
        messages=messages,
        budget=budget,
        version=version,
        response_format=build_response_format(),
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
    )


async def _apply_semantic_cache(prepared: _Prepared, *, model: str) -> _Prepared:
    """Tras la caché exacta: mide o sirve un vecino semántico. Solo el primer turno."""
    settings = get_settings()
    mode = settings.SEMANTIC_CACHE_MODE
    if mode == "off" or prepared.has_prior_turns or prepared.cached is not None:
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
        overrides = self.overrides
        scrubbed = guard_input(self.transcription)
        self.transcription = scrubbed
        model = overrides.model or choose_model(scrubbed, has_history=bool(self.conversation_history))
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
            name="generate-estimate",
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
    _require_complete(first)
    repairs = 0
    answered = first
    usage = _usage_from(first)
    cost = first.cost_usd
    latency = first.latency_ms
    try:
        turn = parse_turn(first.content)
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
        _require_complete(second)
        turn = parse_turn(second.content)
        _require_kind(turn, prepared.has_prior_turns)
        usage = _add_usage(usage, second)
        latency += second.latency_ms
        if second.cost_usd is not None:
            cost = (cost or 0) + second.cost_usd
        answered = second

    if turn.message:
        scrubbed_message, _entities, _engine = redact_pii(turn.message)
        if scrubbed_message != turn.message:
            turn = turn.model_copy(update={"message": scrubbed_message})

    estimation = None
    warnings: list[ValidationIssue] = []
    markdown = None
    if turn.kind in {"estimation", "estimation_update"}:
        # Observabilidad: el span anota los avisos. validate_and_fix es el trabajo del estimador.
        with traced_span("postprocess") as post_span:
            estimation, warnings = validate_and_fix(
                turn.estimation,  # type: ignore[arg-type]
                require_phase_assumptions=prepared.form_detail_level == DetailLevel.DETAILED,
            )
            estimation, guard_warnings = guard_output(estimation)
            warnings = [*warnings, *guard_warnings]
            markdown = render_markdown(estimation)
            _annotate(post_span, output={"warnings": [issue.code for issue in warnings]})

    history_content = to_history_content(turn, estimation)

    # Exacta y semántica solo tras validar y pasar guardrails; solo si es estimable.
    # Se guarda el turno ya postprocesado (mismo JSON que el historial), no el bruto del modelo.
    if cached is None and estimation is not None and estimation.is_estimable:
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
    )
    # Observabilidad: la estimación ya está en result. Esto solo puntúa y registra.
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
        ),
        block,
        transcription,
        [],
        model,
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


def _budget(messages, block: ContextBlock, transcription: str, history: list[ChatMessage], model: str):
    settings = get_settings()
    system = system_text_of(messages)
    # Todo el system salvo los ejemplos, que van aparte: incluye scope, constraints y metadatos.
    before, _, rest = system.partition("<examples>")
    _examples, _, after = rest.partition("</examples>")
    instructions = f"{before}{after}"
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
