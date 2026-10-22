from app.context.preprocessing import build_context_block
from app.schemas.context import ContextBlock
from app.schemas.conversation import ChatMessage
from app.services.prompt_builder import (
    apply_cache_strategy,
    build_messages,
    prompt_version,
    system_text_of,
)


def _block() -> ContextBlock:
    return build_context_block()


def test_system_order_and_user_transcription():
    messages = build_messages("Reunión suficientemente larga para estimar el proyecto descrito.", _block())
    system = system_text_of(messages)
    positions = [
        system.index("<rol_y_tarea>"),
        system.index("<formato_salida>"),
        system.index("<tarifas>"),
        system.index("<ejemplos>"),
        system.index("<restricciones>"),
    ]
    assert positions == sorted(positions)
    assert messages[1]["role"] == "user"
    assert "<transcripcion>" in messages[1]["content"]
    assert "===== ESTIMACIÓN DE REFERENCIA 1 =====" in system


def test_system_is_identical_for_different_transcriptions():
    block = _block()
    first = build_messages("Primera transcripción de la reunión con el cliente, bastante larga.", block)
    second = build_messages("Segunda transcripción, distinta, también lo bastante larga para el mínimo.", block)
    assert system_text_of(first) == system_text_of(second)
    assert prompt_version(system_text_of(first)) == prompt_version(system_text_of(second))


def test_prompt_version_changes_when_context_changes():
    full = build_context_block(max_examples=5)
    short = build_context_block(max_examples=1)
    full_system = system_text_of(build_messages("Transcripción de prueba lo bastante larga.", full))
    short_system = system_text_of(build_messages("Transcripción de prueba lo bastante larga.", short))
    assert prompt_version(full_system) != prompt_version(short_system)


def test_cache_control_only_for_anthropic_auto():
    block = _block()
    messages = build_messages("Transcripción de prueba lo bastante larga para el prompt.", block)
    openai = apply_cache_strategy(messages, "openai", block, "auto")
    assert isinstance(openai[0]["content"], str)
    assert "cache_control" not in openai[0]["content"]

    off = apply_cache_strategy(messages, "anthropic", block, "off")
    assert isinstance(off[0]["content"], str)

    anthropic = apply_cache_strategy(messages, "anthropic", block, "auto")
    blocks = anthropic[0]["content"]
    assert isinstance(blocks, list)
    assert blocks[-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in blocks[0]


def test_dynamic_examples_move_the_cache_breakpoint_before_them():
    block = _block()
    block.examples_are_static = False
    messages = build_messages("Transcripción de prueba lo bastante larga para el prompt.", block)
    anthropic = apply_cache_strategy(messages, "anthropic", block, "auto")
    blocks = anthropic[0]["content"]
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "<ejemplos>" in blocks[1]["text"]
    assert "cache_control" not in blocks[1]


def test_multiturn_reminder_and_second_cache_point():
    history = [
        ChatMessage(role="user", content="Transcripción original del proyecto de inventario para el cliente.", kind="estimation", anchor=True),
        ChatMessage(role="assistant", content='{"kind":"estimation"}', kind="estimation", anchor=True),
    ]
    block = _block()
    messages = build_messages("Sube diseño a 60 horas.", block, history)
    assert messages[-1]["role"] == "user"
    assert "<recordatorio>" in messages[-1]["content"]
    assert "<mensaje_usuario>" in messages[-1]["content"]
    roles = [message["role"] for message in messages if message["role"] != "system"]
    assert roles == ["user", "assistant", "user"]

    cached = apply_cache_strategy(messages, "anthropic", block, "auto")
    assert isinstance(cached[-2]["content"], list)
    assert cached[-2]["content"][0]["cache_control"]["type"] == "ephemeral"
