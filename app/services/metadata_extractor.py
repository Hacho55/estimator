"""Extractor de memoria: una llamada corta que propone un delta.

No fusiona: devuelve un ``MetadataUpdate`` y ``app/state/memory.py`` decide qué
entra. Si la llamada o el JSON fallan, devuelve un delta vacío (la memoria no
cambia) y la latencia del intento, para que el coste no quede oculto.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import structlog

from app.config import get_settings
from app.prompts.loader import render_metadata_prompt
from app.schemas.estimation import UsageInfo
from app.schemas.session import MetadataUpdate, ProjectMetadata
from app.services import llm_client

log = structlog.get_logger(__name__)


@dataclass
class MetadataExtraction:
    update: MetadataUpdate = field(default_factory=MetadataUpdate)
    usage: UsageInfo | None = None
    cost_usd: float | None = None
    latency_ms: float = 0
    ok: bool = True


def metadata_response_format() -> dict:
    if get_settings().LLM_RESPONSE_FORMAT == "json_object":
        return {"type": "json_object"}
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "metadata_update",
            "schema": MetadataUpdate.model_json_schema(),
            "strict": False,
        },
    }


def _usage_of(result: llm_client.LLMResult) -> UsageInfo:
    return UsageInfo(
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        total_tokens=result.total_tokens,
        cached_tokens=result.cached_tokens,
        cache_creation_tokens=result.cache_creation_tokens,
    )


async def extract_metadata_update(
    *,
    previous: ProjectMetadata,
    user_turn: str,
    assistant_turn: str,
) -> MetadataExtraction:
    system, user = render_metadata_prompt(
        previous=previous.model_dump_json(),
        user_turn=user_turn,
        assistant_turn=assistant_turn,
    )
    settings = get_settings()
    started = time.perf_counter()
    try:
        result = await llm_client.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            metadata_response_format(),
            name="extract-metadata",
            model=settings.METADATA_MODEL.strip() or None,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("metadata_extraction_failed", reason=str(exc))
        return MetadataExtraction(latency_ms=(time.perf_counter() - started) * 1000, ok=False)
    usage = _usage_of(result)
    try:
        update = MetadataUpdate.model_validate_json(result.content)
    except Exception as exc:  # noqa: BLE001
        log.warning("metadata_extraction_failed", reason=str(exc))
        return MetadataExtraction(usage=usage, cost_usd=result.cost_usd, latency_ms=result.latency_ms, ok=False)
    return MetadataExtraction(update=update, usage=usage, cost_usd=result.cost_usd, latency_ms=result.latency_ms)
