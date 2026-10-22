"""Historial en memoria, con ventana deslizante y dos anclas.

Se pierde al reiniciar el proceso y exige un solo worker. La interfaz
ConversationStore es la costura para persistirlo más adelante.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.config import get_settings
from app.schemas.conversation import ChatMessage
from app.services.errors import ContextBudgetExceededError, ConversationNotFoundError
from app.tokens import count_tokens

ESTIMATION_KINDS = {"estimation", "estimation_update"}


def select_history(
    messages: list[ChatMessage],
    *,
    max_turns: int,
    room_tokens: int,
) -> tuple[list[ChatMessage], int]:
    """Devuelve los mensajes que caben y cuántos pares se descartaron.

    El primer par (la transcripción) y el último par que contiene una estimación
    no se descartan. Si ni esas anclas caben, la petición no se envía.
    """
    if not messages:
        return [], 0
    usable = messages[: len(messages) - (len(messages) % 2)]
    pairs = [(usable[index], usable[index + 1]) for index in range(0, len(usable), 2)]
    if not pairs:
        return [], 0

    anchor_indexes = {0}
    last_estimation = 0
    for index, pair in enumerate(pairs):
        if pair[1].kind in ESTIMATION_KINDS:
            last_estimation = index
    anchor_indexes.add(last_estimation)

    tail = set(range(max(0, len(pairs) - max_turns), len(pairs)))
    selected = [index for index in range(len(pairs)) if index in anchor_indexes or index in tail]

    def weight(indexes: list[int]) -> int:
        return count_tokens("\n".join(message.content for index in indexes for message in pairs[index]))

    while weight(selected) > room_tokens and any(index not in anchor_indexes for index in selected):
        for index in list(selected):
            if index not in anchor_indexes:
                selected.remove(index)
                break

    if weight(selected) > room_tokens:
        raise ContextBudgetExceededError(
            "La transcripción y la última estimación ya no caben en la ventana de contexto. "
            "Reinicia la conversación con DELETE /api/v1/conversations/{id}."
        )

    kept: list[ChatMessage] = []
    for index in selected:
        kept.extend(pairs[index])
    return kept, len(pairs) - len(selected)


@dataclass
class Conversation:
    id: str
    messages: list[ChatMessage] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ConversationStore:
    def create(self) -> Conversation:
        raise NotImplementedError

    def get(self, conversation_id: str) -> Conversation:
        raise NotImplementedError

    def delete(self, conversation_id: str) -> None:
        raise NotImplementedError

    def lock(self, conversation_id: str) -> asyncio.Lock:
        raise NotImplementedError


class InMemoryConversationStore(ConversationStore):
    def __init__(
        self,
        *,
        ttl_minutes: int,
        max_active: int,
        now=None,
    ) -> None:
        self.ttl = timedelta(minutes=ttl_minutes)
        self.max_active = max_active
        self._now = now or datetime.now
        self._items: dict[str, Conversation] = {}

    @classmethod
    def from_settings(cls) -> InMemoryConversationStore:
        settings = get_settings()
        return cls(
            ttl_minutes=settings.CONVERSATION_TTL_MINUTES,
            max_active=settings.CONVERSATION_MAX_ACTIVE,
        )

    def create(self) -> Conversation:
        self._expire()
        while len(self._items) >= self.max_active:
            oldest = min(self._items.values(), key=lambda item: item.updated_at)
            del self._items[oldest.id]
        conversation = Conversation(id=uuid.uuid4().hex, created_at=self._now(), updated_at=self._now())
        self._items[conversation.id] = conversation
        return conversation

    def get(self, conversation_id: str) -> Conversation:
        self._expire()
        conversation = self._items.get(conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(conversation_id)
        return conversation

    def delete(self, conversation_id: str) -> None:
        if conversation_id not in self._items:
            raise ConversationNotFoundError(conversation_id)
        del self._items[conversation_id]

    def lock(self, conversation_id: str) -> asyncio.Lock:
        return self.get(conversation_id).lock

    def touch(self, conversation: Conversation) -> None:
        conversation.updated_at = self._now()

    def _expire(self) -> None:
        now = self._now()
        expired = [key for key, item in self._items.items() if now - item.updated_at > self.ttl]
        for key in expired:
            del self._items[key]


_store: InMemoryConversationStore | None = None


def get_store() -> InMemoryConversationStore:
    global _store
    if _store is None:
        _store = InMemoryConversationStore.from_settings()
    return _store


def reset_store() -> InMemoryConversationStore:
    global _store
    _store = InMemoryConversationStore.from_settings()
    return _store
