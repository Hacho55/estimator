"""Conteo de tokens y presupuesto de la ventana de contexto."""

from functools import lru_cache

import tiktoken

from app.schemas.context import TokenBlock, TokenBudget

OPENAI_ENCODING = "o200k_base"
CACHE_MIN_TOKENS = 1024


@lru_cache(maxsize=1)
def _encoder() -> tiktoken.Encoding:
    return tiktoken.get_encoding(OPENAI_ENCODING)


def count_tokens(text: str) -> int:
    if not text:
        return 0
    return len(_encoder().encode(text))


def count_exact(model: str) -> bool:
    lowered = model.lower()
    return "gpt" in lowered or "/openai/" in lowered or lowered.startswith("openai")


def build_token_budget(
    *,
    instructions: str,
    context: str,
    transcription: str,
    history: str,
    reserved_output: int,
    safety_margin: int,
    window: int,
    model: str,
    cacheable: str,
) -> TokenBudget:
    blocks = [
        TokenBlock(name="instructions", tokens=count_tokens(instructions)),
        TokenBlock(name="context", tokens=count_tokens(context)),
        TokenBlock(name="history", tokens=count_tokens(history)),
        TokenBlock(name="transcription", tokens=count_tokens(transcription)),
    ]
    total_input = sum(block.tokens for block in blocks)
    used = total_input + reserved_output + safety_margin
    return TokenBudget(
        blocks=blocks,
        total_input=total_input,
        reserved_output=reserved_output,
        safety_margin=safety_margin,
        window=window,
        fits=used <= window,
        exact=count_exact(model),
        per_thousand_calls=total_input * 1000,
        cacheable_tokens=count_tokens(cacheable),
    )
