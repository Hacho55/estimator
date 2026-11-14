"""Ensambla el prompt: orden, segmentos de caché e historial.

La redacción vive en ``app/prompts/estimation/<versión>/``. Aquí solo se elige
la versión, se cortan los segmentos para la caché del prefijo y se une el
historial de la conversación.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from app.config import derive_provider, get_settings
from app.prompts.loader import (
    as_data,
    has_turn_template,
    render_estimation_prompt,
    render_follow_up,
    render_reminder,
    render_repair,
)
from app.schemas.context import ContextBlock
from app.schemas.conversation import ChatMessage
from app.schemas.estimation import (
    DetailLevel,
    OutputFormat,
    PromptMessage,
    PromptPreview,
    PromptRenderRequest,
    TurnOutput,
)
from app.tokens import build_token_budget

DEFAULT_PROMPT_VERSION = "v1"


@dataclass
class DynamicContext:
    """Contexto que cambia por petición (v4). Siempre es dato, nunca instrucción.

    memory va al final del system (fuera de la caché); el resto, en el mensaje de usuario.
    """

    memory: dict | None = None
    attachments: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    critic_feedback: list[dict] = field(default_factory=list)
DEFAULT_DETAIL_LEVEL = DetailLevel.MEDIUM
DEFAULT_OUTPUT_FORMAT = OutputFormat.PHASES_TABLE


MEMORY_TAG = "<project_metadata>"
_CONSTRAINTS_END = "</constraints>"


def _memory_start(system_text: str) -> int:
    """Posición del bloque de memoria: el primer ``<project_metadata>`` tras ``</constraints>``.

    Antes puede aparecer el nombre del bloque (por ejemplo en <scope>), así que no
    basta con la primera aparición. -1 si no hay memoria.
    """
    end = system_text.find(_CONSTRAINTS_END)
    if end < 0:
        return -1
    return system_text.find(MEMORY_TAG, end)


def strip_memory(system_text: str) -> str:
    """El system sin el bloque de memoria final: lo que identifica la plantilla."""
    start = _memory_start(system_text)
    return system_text if start < 0 else system_text[:start]


def prompt_version(system_text: str, version: str = DEFAULT_PROMPT_VERSION) -> str:
    """Versión = carpeta + hash del system sin memoria: los hechos cambian por turno, la plantilla no."""
    # rstrip: con o sin memoria, y como texto o como bloques unidos, el prefijo estable es el mismo.
    digest = hashlib.sha256(strip_memory(system_text).rstrip().encode("utf-8")).hexdigest()[:8]
    return f"{version}-{digest}"


def split_system_segments(system_text: str) -> list[dict[str, str]]:
    """Parte el system renderizado en static / examples / restrictions / memory.

    ``memory`` (si existe) va al final y fuera de la caché del prefijo.
    """
    if "<examples>" not in system_text or "</examples>" not in system_text:
        raise ValueError("El system prompt no contiene el bloque <examples>.")
    before, rest = system_text.split("<examples>", 1)
    examples_body, after = rest.split("</examples>", 1)
    examples = f"<examples>{examples_body}</examples>"
    after = after.strip()
    start = _memory_start(after)
    restrictions = (after if start < 0 else after[:start]).strip()
    memory = "" if start < 0 else after[start:].strip()
    if not (restrictions.startswith("<constraints>") or restrictions.startswith("<scope>")):
        raise ValueError("El system prompt no deja <scope>/<constraints> tras los ejemplos.")
    segments = [
        {"segment": "static", "text": before.rstrip()},
        {"segment": "examples", "text": examples},
        {"segment": "restrictions", "text": restrictions},
    ]
    if memory:
        segments.append({"segment": "memory", "text": memory})
    return segments


def _render_request(
    description: str,
    *,
    detail_level: DetailLevel = DEFAULT_DETAIL_LEVEL,
    output_format: OutputFormat = DEFAULT_OUTPUT_FORMAT,
    project_type=None,
    project_metadata: str = "",
    dynamic: DynamicContext | None = None,
) -> PromptRenderRequest:
    dynamic = dynamic or DynamicContext()
    return PromptRenderRequest(
        description=description,
        project_type=project_type,
        detail_level=detail_level,
        output_format=output_format,
        project_metadata=project_metadata,
        memory=dynamic.memory,
        attachments=dynamic.attachments,
        tool_results=dynamic.tool_results,
        critic_feedback=dynamic.critic_feedback,
    )


def _history_message(message: ChatMessage, *, remind: bool, version: str) -> dict:
    # Lo que escribió el usuario sigue siendo dato al reenviarlo: se neutralizan sus etiquetas.
    text = as_data(message.content) if message.role == "user" else message.content
    if message.role == "user" and remind:
        reminder = render_reminder(version)
        content = f"<reminder>\n{reminder}\n</reminder>\n<user_message>\n{text}\n</user_message>"
    else:
        content = text
    return {"role": message.role, "content": content}


def build_messages(
    transcription: str,
    context_block: ContextBlock,
    conversation_history: list[ChatMessage] | None = None,
    *,
    prompt_label: str = DEFAULT_PROMPT_VERSION,
    detail_level: DetailLevel = DEFAULT_DETAIL_LEVEL,
    output_format: OutputFormat = DEFAULT_OUTPUT_FORMAT,
    project_type=None,
    project_metadata: str = "",
    dynamic: DynamicContext | None = None,
) -> list[dict]:
    request = _render_request(
        transcription,
        detail_level=detail_level,
        output_format=output_format,
        project_type=project_type,
        project_metadata=project_metadata,
        dynamic=dynamic,
    )
    system, user = render_estimation_prompt(
        request,
        prompt_label,
        rates=context_block.static_text,
        examples=context_block.examples_text,
    )
    segments = split_system_segments(system)
    messages: list[dict] = [
        {"role": "system", "content": system, "segments": segments},
    ]
    history = conversation_history or []
    if history:
        for index, message in enumerate(history):
            is_last_user = message.role == "user" and index == len(history) - 1
            messages.append(_history_message(message, remind=is_last_user, version=prompt_label))
        if has_turn_template(prompt_label):
            content = render_follow_up(request, prompt_label)
        else:
            reminder = render_reminder(prompt_label)
            text = as_data(transcription)
            content = f"<reminder>\n{reminder}\n</reminder>\n<user_message>\n{text}\n</user_message>"
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": user})
    return ensure_alternation(messages)


def ensure_alternation(messages: list[dict]) -> list[dict]:
    """Algunos proveedores rechazan dos mensajes seguidos del mismo rol."""
    merged: list[dict] = []
    for message in messages:
        if (
            merged
            and message["role"] != "system"
            and merged[-1]["role"] == message["role"]
            and isinstance(merged[-1]["content"], str)
            and isinstance(message["content"], str)
        ):
            merged[-1]["content"] = f"{merged[-1]['content']}\n\n{message['content']}"
            continue
        merged.append(dict(message))
    return merged


def apply_cache_strategy(
    messages: list[dict],
    provider: str,
    context_block: ContextBlock,
    prompt_cache: str | None = None,
) -> list[dict]:
    mode = prompt_cache or get_settings().LLM_PROMPT_CACHE
    prepared = [dict(message) for message in messages]
    system = prepared[0]
    segments = system.pop("segments", None)
    if segments is None:
        return prepared

    use_explicit = mode == "auto" and provider == "anthropic"
    if not use_explicit:
        system["content"] = "\n\n".join(segment["text"] for segment in segments)
        return prepared

    blocks = [{"type": "text", "text": segment["text"]} for segment in segments]
    # La memoria (si está) va después del punto de caché: cambia por turno.
    stable = [index for index, segment in enumerate(segments) if segment["segment"] != "memory"]
    if context_block.examples_are_static:
        blocks[stable[-1]]["cache_control"] = {"type": "ephemeral"}
    else:
        blocks[0]["cache_control"] = {"type": "ephemeral"}
    system["content"] = blocks

    # Punto de caché en el último mensaje del historial: si la memoria no cambió entre
    # turnos, el siguiente turno reutiliza todo el prefijo. Si cambió, se escribe uno nuevo;
    # el prefijo de la plantilla (punto anterior) sigue cacheado igual.
    if len(prepared) > 2:
        previous = prepared[-2]
        if isinstance(previous["content"], str):
            previous["content"] = [
                {
                    "type": "text",
                    "text": previous["content"],
                    "cache_control": {"type": "ephemeral"},
                }
            ]
    return prepared


def build_response_format() -> dict:
    if get_settings().LLM_RESPONSE_FORMAT == "json_object":
        return {"type": "json_object"}
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "turn_output",
            "schema": TurnOutput.model_json_schema(),
            "strict": False,
        },
    }


def system_text_of(messages: list[dict]) -> str:
    content = messages[0]["content"]
    if isinstance(content, str):
        return content
    return "\n\n".join(block.get("text", "") for block in content)


def cacheable_text(messages: list[dict], context_block: ContextBlock) -> str:
    """Lo que entra en la caché del prefijo. Nunca incluye la memoria."""
    content = system_text_of(messages)
    if context_block.examples_are_static:
        return strip_memory(content)
    return content.split("<examples>", 1)[0]


def repair_instruction(error: str, version: str = DEFAULT_PROMPT_VERSION) -> str:
    return render_repair(version, error=error)


def build_preview(
    transcription: str,
    context_block: ContextBlock | None = None,
    *,
    prompt_label: str = DEFAULT_PROMPT_VERSION,
    detail_level: DetailLevel = DEFAULT_DETAIL_LEVEL,
    output_format: OutputFormat = DEFAULT_OUTPUT_FORMAT,
    project_type=None,
) -> PromptPreview:
    from app.context.preprocessing import get_context_block

    block = context_block or get_context_block(transcription)
    settings = get_settings()
    provider = derive_provider(settings.LLM_MODEL)
    messages = apply_cache_strategy(
        build_messages(
            transcription,
            block,
            prompt_label=prompt_label,
            detail_level=detail_level,
            output_format=output_format,
            project_type=project_type,
        ),
        provider,
        block,
        settings.LLM_PROMPT_CACHE,
    )
    prefix = system_text_of(messages)
    budget = build_token_budget(
        instructions=prefix.split("<examples>", 1)[0],
        context=block.examples_text,
        transcription=transcription,
        history="",
        reserved_output=settings.LLM_MAX_TOKENS,
        safety_margin=settings.CONTEXT_SAFETY_MARGIN_TOKENS,
        window=settings.MODEL_CONTEXT_WINDOW,
        model=settings.LLM_MODEL,
        cacheable=cacheable_text(messages, block),
    )
    visible = []
    for message in messages:
        content = message["content"]
        visible.append(PromptMessage(role=message["role"], content=content))
    versioned = prompt_version(
        prefix if block.examples_are_static else cacheable_text(messages, block),
        prompt_label,
    )
    return PromptPreview(
        messages=visible,
        prompt_version=versioned,
        token_budget=budget,
    )
