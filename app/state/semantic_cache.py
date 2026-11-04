"""Caché semántica por bucket exacto + vector de la descripción.

Por defecto usa un índice en memoria (tests y degradación). Con REDIS_URL y
redisvl intenta Redis; si falla, la petición sigue sin caché semántica.
"""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass

import structlog

from app.config import get_settings
from app.observability import log_event, record_scores

log = structlog.get_logger(__name__)

TTL_SECONDS = 24 * 60 * 60
MAX_ENTRIES = 256
INDEX_NAME = "estimation_cache"


@dataclass
class SemanticHit:
    content: str
    model: str
    finish_reason: str | None
    similarity: float
    entry_id: str


@dataclass
class _MemoryEntry:
    bucket: str
    vector: list[float]
    content: str
    model: str
    finish_reason: str | None
    entry_id: str
    expires_at: float


_memory: list[_MemoryEntry] = []
_redis_cache = None
_redis_failed = False


def reset_semantic_cache() -> None:
    global _memory, _redis_cache, _redis_failed
    _memory = []
    _redis_cache = None
    _redis_failed = False


def bucket_id(
    *,
    prompt_version: str,
    project_type: str | None,
    detail_level: str | None,
    output_format: str | None,
    model: str,
) -> str:
    parts = [
        prompt_version,
        project_type or "-",
        detail_level or "-",
        output_format or "-",
        model,
    ]
    return "|".join(parts)


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm_l = math.sqrt(sum(a * a for a in left))
    norm_r = math.sqrt(sum(b * b for b in right))
    if norm_l == 0 or norm_r == 0:
        return 0.0
    return dot / (norm_l * norm_r)


def _expire_memory(now: float) -> None:
    global _memory
    _memory = [entry for entry in _memory if entry.expires_at > now]


def _memory_lookup(bucket: str, vector: list[float], threshold: float) -> SemanticHit | None:
    now = time.time()
    _expire_memory(now)
    best: SemanticHit | None = None
    for entry in _memory:
        if entry.bucket != bucket:
            continue
        score = _cosine(vector, entry.vector)
        if score < threshold:
            continue
        if best is None or score > best.similarity:
            best = SemanticHit(
                content=entry.content,
                model=entry.model,
                finish_reason=entry.finish_reason,
                similarity=score,
                entry_id=entry.entry_id,
            )
    return best


def _memory_store(
    *,
    bucket: str,
    vector: list[float],
    content: str,
    model: str,
    finish_reason: str | None,
) -> str:
    now = time.time()
    _expire_memory(now)
    entry_id = uuid.uuid4().hex
    _memory.append(
        _MemoryEntry(
            bucket=bucket,
            vector=vector,
            content=content,
            model=model,
            finish_reason=finish_reason,
            entry_id=entry_id,
            expires_at=now + TTL_SECONDS,
        )
    )
    while len(_memory) > MAX_ENTRIES:
        _memory.pop(0)
    return entry_id


def _value(field: object | None) -> str | None:
    if field is None:
        return None
    return field.value if hasattr(field, "value") else str(field)


def _redis_client():
    global _redis_cache, _redis_failed
    if _redis_failed:
        return None
    if _redis_cache is not None:
        return _redis_cache
    settings = get_settings()
    if not settings.REDIS_URL:
        return None
    try:
        from redisvl.extensions.cache.llm import SemanticCache
        from redisvl.query.filter import Tag
        from redisvl.utils.vectorize import CustomTextVectorizer

        dims = settings.EMBEDDING_DIMS

        def _index_embed(_text: str) -> list[float]:
            # Solo para dimensionar el índice; las búsquedas pasan el vector real.
            return [0.0] * dims

        vectorizer = CustomTextVectorizer(embed=_index_embed)
        threshold = max(0.0, min(1.0, 1.0 - settings.SEMANTIC_CACHE_THRESHOLD))
        cache = SemanticCache(
            name=INDEX_NAME,
            redis_url=settings.REDIS_URL,
            ttl=TTL_SECONDS,
            distance_threshold=threshold,
            vectorizer=vectorizer,
            filterable_fields=[{"name": "bucket", "type": "tag"}],
            overwrite=False,
        )
        _redis_cache = (cache, Tag)
        from urllib.parse import urlparse

        parsed = urlparse(settings.REDIS_URL)
        host = parsed.hostname or "unknown"
        db = (parsed.path or "/").lstrip("/") or "0"
        log.info("semantic_cache_redis_ready", host=host, db=db)
        return _redis_cache
    except Exception as exc:  # noqa: BLE001
        _redis_failed = True
        log.warning("semantic_cache_redis_unavailable", reason=str(exc))
        return None


def lookup_semantic(
    *,
    bucket: str,
    vector: list[float],
) -> SemanticHit | None:
    settings = get_settings()
    threshold = settings.SEMANTIC_CACHE_THRESHOLD
    redis = _redis_client()
    if redis is not None:
        cache, tag_cls = redis
        try:
            hits = cache.check(
                vector=vector,
                num_results=1,
                filter_expression=tag_cls("bucket") == bucket,
            )
            if hits:
                item = hits[0]
                distance = float(item.get("vector_distance", item.get("distance", 1.0)))
                similarity = 1.0 - distance
                if similarity >= threshold:
                    return SemanticHit(
                        content=str(item.get("response") or item.get("content") or ""),
                        model=str((item.get("metadata") or {}).get("model") or item.get("model") or ""),
                        finish_reason=(item.get("metadata") or {}).get("finish_reason"),
                        similarity=similarity,
                        entry_id=str(item.get("id") or item.get("entry_id") or ""),
                    )
            return None
        except Exception as exc:  # noqa: BLE001
            log.warning("semantic_cache_lookup_failed", reason=str(exc))
            return None
    return _memory_lookup(bucket, vector, threshold)


def store_semantic(
    *,
    bucket: str,
    vector: list[float],
    prompt: str,
    content: str,
    model: str,
    finish_reason: str | None,
) -> str | None:
    redis = _redis_client()
    if redis is not None:
        cache, _tag = redis
        try:
            return cache.store(
                prompt=prompt,
                response=content,
                vector=vector,
                metadata={"model": model, "finish_reason": finish_reason},
                filters={"bucket": bucket},
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("semantic_cache_store_failed", reason=str(exc))
            return None
    return _memory_store(
        bucket=bucket,
        vector=vector,
        content=content,
        model=model,
        finish_reason=finish_reason,
    )


def emit_semantic_check(
    *,
    bucket: str,
    hit: SemanticHit | None,
    would_hit: bool,
    mode: str,
) -> None:
    log_event(
        "semantic_cache_checked",
        bucket=bucket,
        mode=mode,
        would_hit=would_hit,
        score=None if hit is None else hit.similarity,
        neighbor_id=None if hit is None else hit.entry_id,
    )
    if hit is not None:
        record_scores({"semantic_cache.similarity": hit.similarity})


@dataclass
class SemanticBucketFields:
    prompt_version: str
    project_type: object | None = None
    detail_level: object | None = None
    output_format: object | None = None
    model: str = ""

    def key(self) -> str:
        return bucket_id(
            prompt_version=self.prompt_version,
            project_type=_value(self.project_type),
            detail_level=_value(self.detail_level),
            output_format=_value(self.output_format),
            model=self.model,
        )
