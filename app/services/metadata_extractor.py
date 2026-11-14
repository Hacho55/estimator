"""Segunda llamada corta. Si falla, la estimación sigue y los hechos no cambian."""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from app.config import get_settings
from app.prompts.loader import render_metadata_prompt
from app.schemas.estimation import UsageInfo
from app.schemas.session import ExtractedMetadata, ProjectMetadata
from app.services import llm_client

log = structlog.get_logger(__name__)


@dataclass
class MetadataExtraction:
    metadata: ProjectMetadata
    usage: UsageInfo | None = None
    cost_usd: float | None = None
    latency_ms: float = 0


def metadata_response_format() -> dict:
    if get_settings().LLM_RESPONSE_FORMAT == "json_object":
        return {"type": "json_object"}
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "project_metadata",
            "schema": ExtractedMetadata.model_json_schema(),
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


async def extract_project_metadata(
    *,
    previous: ProjectMetadata,
    turn: str,
    summary: str,
) -> MetadataExtraction:
    system, user = render_metadata_prompt(
        previous=previous.model_dump_json(),
        turn=turn,
        summary=summary,
    )
    try:
        result = await llm_client.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            metadata_response_format(),
            name="extract-metadata",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("metadata_extraction_failed", reason=str(exc))
        return MetadataExtraction(metadata=previous)
    usage = _usage_of(result)
    try:
        metadata = ExtractedMetadata.model_validate_json(result.content).to_metadata()
    except Exception as exc:  # noqa: BLE001
        log.warning("metadata_extraction_failed", reason=str(exc))
        return MetadataExtraction(metadata=previous, usage=usage, cost_usd=result.cost_usd, latency_ms=result.latency_ms)
    return MetadataExtraction(metadata=metadata, usage=usage, cost_usd=result.cost_usd, latency_ms=result.latency_ms)
