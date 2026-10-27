"""Endpoints de estimación, diagnóstico de contexto y conversación."""

from fastapi import APIRouter, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.config import get_settings
from app.schemas.context import ContextInfo
from app.schemas.conversation import (
    ChatMessage,
    ConversationCreateRequest,
    ConversationDetail,
    ConversationMessageRequest,
    ConversationState,
    ConversationTurnResponse,
)
from app.schemas.estimation import (
    ErrorResponse,
    EstimationRequest,
    EstimationResponse,
    PromptPreview,
)
from app.services.errors import ServiceError
from app.services.llm_service import generate_estimation, get_context_info, preview_prompt, stream_estimation
from app.state.conversations import get_store

router = APIRouter(prefix="/api/v1", tags=["estimations"])

_ERRORS = {
    413: {"model": ErrorResponse},
    502: {"model": ErrorResponse},
    504: {"model": ErrorResponse},
}


def _state(turns_total: int, turns_in_window: int, turns_dropped: int, input_tokens: int) -> ConversationState:
    settings = get_settings()
    usable = max(settings.MODEL_CONTEXT_WINDOW - settings.CONTEXT_SAFETY_MARGIN_TOKENS, 1)
    return ConversationState(
        turns_total=turns_total,
        turns_in_window=turns_in_window,
        turns_dropped=turns_dropped,
        approx_input_tokens=input_tokens,
        near_limit=input_tokens / usable >= settings.CONVERSATION_NEAR_LIMIT_RATIO,
    )


def _turn_response(conversation_id: str, turn: int, result, state: ConversationState) -> ConversationTurnResponse:
    return ConversationTurnResponse(
        conversation_id=conversation_id,
        turn=turn,
        kind=result.kind,  # type: ignore[arg-type]
        message=result.message,
        estimation=result.estimation_markdown,
        estimation_structured=result.estimation,
        warnings=result.warnings,
        conversation_state=state,
        model=result.model,
        provider=result.provider,
        prompt_version=result.prompt_version,
        token_budget=result.token_budget,
        usage=result.usage,
        cost_usd=result.cost_usd,
        latency_ms=result.latency_ms,
        cache_hit=result.cache_hit,
        trace_id=result.trace_id,
    )


@router.post(
    "/estimate",
    response_model=EstimationResponse,
    operation_id="create_estimation",
    responses=_ERRORS,
)
async def estimate(request: EstimationRequest) -> EstimationResponse:
    result = await generate_estimation(request.transcription)
    return result.to_estimation_response()


def _estimate_event(result) -> dict:
    usage = result.usage
    return {
        "estimation": result.estimation_markdown,
        "model": result.model,
        "provider": result.provider,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "latency_ms": result.latency_ms,
        "finish_reason": result.finish_reason,
        "cache_hit": result.cache_hit,
        "prompt_version": result.prompt_version,
    }


@router.post(
    "/estimate/stream",
    operation_id="stream_estimate",
    response_class=EventSourceResponse,
    responses=_ERRORS,
)
async def estimate_stream(request: EstimationRequest):
    """Sesión 3 · sse: trozos para quien no habla con Python. El POST completo no cambia."""
    stream = stream_estimation(request.transcription)
    try:
        async for piece in stream:
            if stream.from_cache:
                continue
            yield ServerSentEvent(data=piece, event="token")
        result = stream.result
        if result is None:
            yield ServerSentEvent(data={"error": "service_error", "detail": "Sin estimación."}, event="error")
            return
        yield ServerSentEvent(data=_estimate_event(result), event="estimate")
    except ServiceError as exc:
        yield ServerSentEvent(data={"error": exc.code, "detail": exc.message}, event="error")


@router.post(
    "/estimate/preview",
    response_model=PromptPreview,
    operation_id="preview_estimation_prompt",
    include_in_schema=get_settings().APP_ENV == "development",
)
async def preview_estimation(request: EstimationRequest) -> PromptPreview:
    if get_settings().APP_ENV != "development":
        raise HTTPException(status_code=404, detail="Disponible solo en development.")
    return preview_prompt(request.transcription)


@router.get("/context", response_model=ContextInfo, operation_id="get_context_info")
async def context_info(include_text: bool = False) -> ContextInfo:
    return get_context_info(include_text=include_text)


@router.post(
    "/conversations",
    response_model=ConversationTurnResponse,
    operation_id="create_conversation",
    responses=_ERRORS,
)
async def create_conversation(request: ConversationCreateRequest) -> ConversationTurnResponse:
    store = get_store()
    conversation = store.create()
    async with conversation.lock:
        result = await generate_estimation(request.transcription, session_id=conversation.id)
        conversation.messages.append(
            ChatMessage(role="user", content=request.transcription, kind="estimation", anchor=True)
        )
        conversation.messages.append(
            ChatMessage(
                role="assistant",
                content=result.history_content,
                kind=result.kind,
                anchor=result.kind in {"estimation", "estimation_update"},
            )
        )
        store.touch(conversation)
        pairs = len(conversation.messages) // 2
        state = _state(pairs, pairs - result.dropped_turns, result.dropped_turns, result.usage.input_tokens)
        return _turn_response(conversation.id, pairs, result, state)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=ConversationTurnResponse,
    operation_id="send_conversation_message",
    responses={**_ERRORS, 404: {"model": ErrorResponse}},
)
async def send_message(conversation_id: str, request: ConversationMessageRequest) -> ConversationTurnResponse:
    store = get_store()
    async with store.lock(conversation_id):
        conversation = store.get(conversation_id)
        result = await generate_estimation(
            request.message,
            conversation_history=list(conversation.messages),
            session_id=conversation.id,
        )
        conversation.messages.append(ChatMessage(role="user", content=request.message, kind=result.kind))
        conversation.messages.append(
            ChatMessage(
                role="assistant",
                content=result.history_content,
                kind=result.kind,
                anchor=result.kind in {"estimation", "estimation_update"},
            )
        )
        store.touch(conversation)
        pairs = len(conversation.messages) // 2
        in_window = pairs - result.dropped_turns
        state = _state(pairs, in_window, result.dropped_turns, result.usage.input_tokens)
        return _turn_response(conversation.id, pairs, result, state)


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetail,
    operation_id="get_conversation",
    responses={404: {"model": ErrorResponse}},
)
async def get_conversation(conversation_id: str) -> ConversationDetail:
    conversation = get_store().get(conversation_id)
    pairs = len(conversation.messages) // 2
    return ConversationDetail(
        conversation_id=conversation.id,
        messages=conversation.messages,
        conversation_state=_state(pairs, pairs, 0, 0),
    )


@router.delete(
    "/conversations/{conversation_id}",
    operation_id="delete_conversation",
    status_code=204,
    responses={404: {"model": ErrorResponse}},
)
async def delete_conversation(conversation_id: str) -> None:
    get_store().delete(conversation_id)
