"""Construye el prompt en el orden que pide el curso y marca la caché del prefijo.

El system prompt se divide en segmentos estables. En CAG los ejemplos también son
estables y entran en el prefijo cacheable. Cuando dejen de serlo (RAG), el punto
de caché queda justo antes de los ejemplos.
"""

from __future__ import annotations

import hashlib

from app.config import derive_provider, get_settings
from app.schemas.context import ContextBlock
from app.schemas.conversation import ChatMessage
from app.schemas.estimation import PromptMessage, PromptPreview, TurnOutput
from app.tokens import build_token_budget

PROMPT_LABEL = "v1"

ROLE_AND_TASK = """\
Eres un consultor senior de software con 15 años de experiencia en estimación de proyectos.
Tu trabajo es analizar transcripciones de reuniones con clientes y generar estimaciones
detalladas de desarrollo de software, calibradas con la realidad de esta empresa."""

CONTEXT_USAGE = """\
A continuación se incluyen la tabla de tarifas de la empresa y estimaciones de proyectos
anteriores. Úsalas como referencia para calibrar tres cosas: los precios por hora, la
granularidad del desglose de tareas y la estructura del presupuesto. No copies un ejemplo:
elige el parecido por tipo de proyecto y adapta el alcance a lo que dice esta transcripción.
Si ningún ejemplo se parece, dilo en los supuestos y estima con la tabla de tarifas."""

OUTPUT_FORMAT = """\
Responde solo con un objeto JSON, sin markdown y sin texto alrededor. El objeto tiene:
- kind: "estimation" en la primera estimación, "estimation_update" si ajustas una estimación
  ya dada, o "answer" si la petición es una pregunta y no un cambio de la estimación.
- message: con kind "answer", la respuesta en texto; con "estimation_update", un resumen
  breve de qué cambió; con "estimation", una frase de cierre.
- estimation: obligatoria salvo cuando kind es "answer". Debe incluir:
  1. project_title.
  2. summary: resumen del proyecto en 2 o 3 frases.
  3. is_estimable y, si es false, not_estimable_reason. Márcala false solo cuando el texto
     no describe un proyecto de software.
  4. tasks: desglose con name, phase, role (clave de la tabla de tarifas), hours, rate_eur
     y cost_eur.
  5. total_hours y total_cost_eur.
  6. team: role, count y dedication (de 0 a 1).
  7. duration_weeks_min y duration_weeks_max.
  8. assumptions y risks, cada uno con al menos un elemento si el proyecto es estimable.
Usa EUR como moneda. Redondea las horas a múltiplos de 5. El coste de cada tarea es horas
por tarifa. No inventes datos del cliente que la transcripción no contenga: si falta
información, declárala como supuesto."""

CONSTRAINTS = """\
Reglas de cierre:
- Estima solo lo que la transcripción describe. Lo que no esté dicho no se presupuesta.
- No mezcles información de un ejemplo de referencia con otro.
- Si ya hay una estimación en la conversación, conserva lo que el usuario no haya pedido cambiar.
- Si la respuesta anterior no era válida, esta debe ser solo el JSON corregido."""

REMINDER = (
    "Responde únicamente con el JSON pedido. kind es estimation, estimation_update o answer. "
    "Horas en múltiplos de 5 y tarifas de la tabla, en EUR."
)

REPAIR_INSTRUCTION = (
    "La respuesta anterior no es un JSON válido para el esquema pedido. "
    "Error: {error}. Devuelve solo el JSON corregido, sin markdown ni explicación."
)


def _system_segments(context_block: ContextBlock) -> list[dict[str, str]]:
    instructions = "\n\n".join(
        [
            f"<role_and_task>\n{ROLE_AND_TASK}\n</role_and_task>",
            f"<context_usage>\n{CONTEXT_USAGE}\n</context_usage>",
            f"<output_format>\n{OUTPUT_FORMAT}\n</output_format>",
            f"<rates>\n{context_block.static_text}\n</rates>",
        ]
    )
    return [
        {"segment": "static", "text": instructions},
        {"segment": "examples", "text": f"<examples>\n{context_block.examples_text}\n</examples>"},
        {"segment": "restrictions", "text": f"<constraints>\n{CONSTRAINTS}\n</constraints>"},
    ]


def prompt_version(system_text: str) -> str:
    digest = hashlib.sha256(system_text.encode("utf-8")).hexdigest()[:8]
    return f"{PROMPT_LABEL}-{digest}"


def _history_message(message: ChatMessage, *, remind: bool) -> dict:
    if message.role == "user" and remind:
        content = f"<reminder>\n{REMINDER}\n</reminder>\n<user_message>\n{message.content}\n</user_message>"
    else:
        content = message.content
    return {"role": message.role, "content": content}


def build_messages(
    transcription: str,
    context_block: ContextBlock,
    conversation_history: list[ChatMessage] | None = None,
) -> list[dict]:
    segments = _system_segments(context_block)
    system_text = "\n\n".join(segment["text"] for segment in segments)
    messages: list[dict] = [
        {"role": "system", "content": system_text, "segments": segments},
    ]
    history = conversation_history or []
    if history:
        for index, message in enumerate(history):
            is_last_user = message.role == "user" and index == len(history) - 1
            messages.append(_history_message(message, remind=is_last_user))
        messages.append(
            {
                "role": "user",
                "content": (
                    f"<reminder>\n{REMINDER}\n</reminder>\n"
                    f"<user_message>\n{transcription}\n</user_message>"
                ),
            }
        )
    else:
        messages.append(
            {
                "role": "user",
                "content": (
                    f"<transcription>\n{transcription}\n</transcription>\n"
                    "Genera la estimación de este proyecto."
                ),
            }
        )
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
    if context_block.examples_are_static:
        blocks[-1]["cache_control"] = {"type": "ephemeral"}
    else:
        blocks[0]["cache_control"] = {"type": "ephemeral"}
    system["content"] = blocks

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
    content = messages[0]["content"]
    if isinstance(content, str):
        if context_block.examples_are_static:
            return content
        return content.split("<examples>", 1)[0]
    if context_block.examples_are_static:
        return "\n\n".join(block.get("text", "") for block in content)
    return content[0].get("text", "") if content else ""


def build_preview(transcription: str, context_block: ContextBlock | None = None) -> PromptPreview:
    from app.context.preprocessing import get_context_block

    block = context_block or get_context_block(transcription)
    settings = get_settings()
    provider = derive_provider(settings.LLM_MODEL)
    messages = apply_cache_strategy(
        build_messages(transcription, block), provider, block, settings.LLM_PROMPT_CACHE
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
    return PromptPreview(
        messages=visible,
        prompt_version=prompt_version(prefix if block.examples_are_static else cacheable_text(messages, block)),
        token_budget=budget,
    )
