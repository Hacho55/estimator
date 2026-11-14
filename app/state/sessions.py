"""Sesiones en memoria del proceso. Sin base de datos y sin Redis.

Se pierden al reiniciar y exigen un solo worker. En esta fase se acepta esa
volatilidad: el ejercicio pide separar historial y hechos del proyecto, no
persistirlos.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.config import get_settings
from app.schemas.conversation import ChatMessage
from app.schemas.session import MemoryChange, ProjectMetadata
from app.services.errors import SessionNotFoundError
from app.tokens import count_tokens


class ConversationHistory:
    """Pares user/assistant con ventana deslizante. El system prompt no se guarda."""

    def __init__(self, max_turns: int) -> None:
        self.max_turns = max_turns
        self.messages: list[ChatMessage] = []

    def turn_count(self) -> int:
        return len(self.messages) // 2

    def append(self, user: str, assistant: str, kind: str | None) -> None:
        self.messages.append(ChatMessage(role="user", content=user))
        self.messages.append(ChatMessage(role="assistant", content=assistant, kind=kind))
        extra = self.turn_count() - self.max_turns
        if extra > 0:
            del self.messages[: extra * 2]

    def to_messages_list(self, system_prompt: str) -> list[dict]:
        """Array listo para el modelo: system regenerado y, como mucho, max_turns pares."""
        rendered = [{"role": "system", "content": system_prompt}]
        for message in self.messages:
            item = {"role": message.role, "content": message.content}
            if message.kind:
                item["kind"] = message.kind
            rendered.append(item)
        return rendered


def slide_history(messages: list[ChatMessage], *, room_tokens: int) -> tuple[list[ChatMessage], int]:
    """Tira los pares más viejos hasta que el texto quepa. No conserva anclas."""
    usable = messages[: len(messages) - (len(messages) % 2)]
    pairs = [(usable[index], usable[index + 1]) for index in range(0, len(usable), 2)]
    if not pairs:
        return [], 0

    def weight(indexes: list[int]) -> int:
        return count_tokens("\n".join(message.content for index in indexes for message in pairs[index]))

    selected = list(range(len(pairs)))
    while selected and weight(selected) > room_tokens:
        selected.pop(0)
    kept: list[ChatMessage] = []
    for index in selected:
        kept.extend(pairs[index])
    return kept, len(pairs) - len(selected)


@dataclass
class Session:
    id: str
    history: ConversationHistory
    metadata: ProjectMetadata = field(default_factory=ProjectMetadata)
    memory_log: list[MemoryChange] = field(default_factory=list)  # Auditoría de la memoria.
    turns: int = 0  # Turnos completados en toda la vida de la sesión (la ventana no lo recorta).
    project_type: object | None = None
    form_detail_level: object | None = None
    output_format: object | None = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class InMemorySessionStore:
    """Misma caducidad y cupo que las conversaciones. Sigue siendo memoria del proceso."""

    def __init__(self, *, ttl_minutes: int, max_active: int, now=None) -> None:
        self.ttl = timedelta(minutes=ttl_minutes)
        self.max_active = max_active
        self._now = now or datetime.now
        self._items: dict[str, Session] = {}

    @classmethod
    def from_settings(cls) -> InMemorySessionStore:
        settings = get_settings()
        return cls(
            ttl_minutes=settings.CONVERSATION_TTL_MINUTES,
            max_active=settings.CONVERSATION_MAX_ACTIVE,
        )

    def create(self) -> Session:
        self._expire()
        while len(self._items) >= self.max_active:
            idle = [item for item in self._items.values() if not item.lock.locked()]
            if not idle:
                # Todas atienden un turno: se acepta pasar el cupo antes que perder una en vuelo.
                break
            oldest = min(idle, key=lambda item: item.updated_at)
            del self._items[oldest.id]
        now = self._now()
        session = Session(
            id=str(uuid.uuid4()),
            history=ConversationHistory(max_turns=get_settings().SESSION_MAX_TURNS),
            created_at=now,
            updated_at=now,
        )
        self._items[session.id] = session
        return session

    def get(self, session_id: str) -> Session:
        self._expire()
        session = self._items.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        self.touch(session)
        return session

    def touch(self, session: Session) -> None:
        session.updated_at = self._now()

    def _expire(self) -> None:
        now = self._now()
        expired = [
            key
            for key, item in self._items.items()
            if now - item.updated_at > self.ttl and not item.lock.locked()
        ]
        for key in expired:
            del self._items[key]


_store: InMemorySessionStore | None = None


def get_session_store() -> InMemorySessionStore:
    global _store
    if _store is None:
        _store = InMemorySessionStore.from_settings()
    return _store


def reset_session_store() -> InMemorySessionStore:
    global _store
    _store = InMemorySessionStore.from_settings()
    return _store
