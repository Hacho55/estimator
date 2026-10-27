"""Sesión 3 · caché: la misma petición devuelve la estimación guardada y no llama al modelo.

La clave junta transcripción, modelo, temperatura, versión del prompt y el historial.
Sin el historial, un turno de conversación podría devolver la estimación de otro turno.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass

TTL_SECONDS = 24 * 60 * 60


@dataclass
class CachedCompletion:
    content: str
    model: str
    finish_reason: str | None
    expires_at: float


_entries: dict[str, CachedCompletion] = {}


def cache_key(
    *,
    transcription: str,
    model: str,
    temperature: float,
    prompt_version: str,
    history: list,
) -> str:
    payload = {
        "transcription": transcription,
        "model": model,
        "temperature": temperature,
        "prompt_version": prompt_version,
        "history": [{"role": message.role, "content": message.content} for message in history],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def lookup(key: str) -> CachedCompletion | None:
    entry = _entries.get(key)
    if entry is None:
        return None
    if entry.expires_at <= time.monotonic():
        _entries.pop(key, None)
        return None
    return entry


def store(key: str, *, content: str, model: str, finish_reason: str | None) -> None:
    _entries[key] = CachedCompletion(
        content=content,
        model=model,
        finish_reason=finish_reason,
        expires_at=time.monotonic() + TTL_SECONDS,
    )


def clear_response_cache() -> None:
    _entries.clear()
