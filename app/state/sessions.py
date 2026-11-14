"""Sesiones en memoria del proceso. Sin base de datos y sin Redis.

Se pierden al reiniciar y exigen un solo worker. En esta fase se acepta esa
volatilidad: la persistencia es del módulo de despliegue. Lo que sí se fija aquí
es el contrato (``SessionStore``) y que historial y memoria se serializan por
separado, para que migrar a Redis no obligue a tocar el modelo.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal, Protocol

from app.config import get_settings
from app.schemas.conversation import ChatMessage
from app.schemas.session import MemoryChange, ProjectMetadata
from app.services.errors import SessionArchivedError, SessionNotFoundError
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


SessionStatus = Literal["active", "archived"]


@dataclass
class Session:
    """Raíz del estado. Historial (lo bruto) y memoria (lo destilado) son piezas separadas."""

    id: str
    history: ConversationHistory
    metadata: ProjectMetadata = field(default_factory=ProjectMetadata)
    memory_log: list[MemoryChange] = field(default_factory=list)  # Auditoría de la memoria.
    turns: int = 0  # Turnos completados en toda la vida de la sesión (la ventana no lo recorta).
    last_tier: str | None = None  # Perfil del último turno, para auditar cambios.
    owner: str = "anonymous"  # user_id del token que la creó; solo él puede usarla.
    status: SessionStatus = "active"
    archived_reason: str | None = None
    inherited_from: str | None = None
    project_type: object | None = None
    form_detail_level: object | None = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    archived_at: datetime | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def to_record(self) -> dict:
        """Serialización para un almacén persistente. Historial y memoria van por separado:
        se pueden guardar, recortar o migrar sin tocar el otro (antipatrón 3 de la fase 2)."""
        return {
            "session": {
                "id": self.id,
                "owner": self.owner,
                "status": self.status,
                "archived_reason": self.archived_reason,
                "inherited_from": self.inherited_from,
                "turns": self.turns,
                "last_tier": self.last_tier,
                "project_type": _plain(self.project_type),
                "detail_level": _plain(self.form_detail_level),
                "created_at": self.created_at.isoformat(),
                "updated_at": self.updated_at.isoformat(),
                "archived_at": self.archived_at.isoformat() if self.archived_at else None,
            },
            "history": [message.model_dump() for message in self.history.messages],
            "memory": self.metadata.model_dump(),
            "memory_log": [change.model_dump() for change in self.memory_log],
        }


def _plain(value: object | None) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


class SessionStore(Protocol):
    """Contrato del almacén. Hoy, memoria del proceso; mañana, Redis o PostgreSQL sin tocar el modelo."""

    def create(self, owner: str = "anonymous") -> Session: ...

    def get(self, session_id: str, owner: str = "anonymous", *, allow_archived: bool = False) -> Session: ...

    def archive(self, session: Session, reason: str, *, at: datetime | None = None) -> None: ...

    def ensure_current(self, session: Session) -> None: ...

    def touch(self, session: Session) -> None: ...


class InMemorySessionStore:
    """Sesiones en memoria del proceso, con olvido explícito (política 2 de la fase 2).

    - Inactiva más de ``idle_archive`` → archivada: se conserva para auditoría pero no
      admite turnos (409). El usuario puede abrir otra heredando su memoria.
    - Archivada más de ``archive_retention`` → se purga.
    - Con el cupo lleno se purgan primero las archivadas, después las activas más viejas.
      Nunca una con un turno en vuelo.
    """

    def __init__(
        self,
        *,
        max_active: int,
        idle_archive: timedelta = timedelta(hours=24),
        archive_retention: timedelta = timedelta(hours=72),
        now=None,
    ) -> None:
        self.max_active = max_active
        self.idle_archive = idle_archive
        self.archive_retention = archive_retention
        self._now = now or datetime.now
        self._items: dict[str, Session] = {}

    @classmethod
    def from_settings(cls) -> InMemorySessionStore:
        settings = get_settings()
        return cls(
            max_active=settings.CONVERSATION_MAX_ACTIVE,
            idle_archive=timedelta(hours=settings.SESSION_IDLE_ARCHIVE_HOURS),
            archive_retention=timedelta(hours=settings.SESSION_ARCHIVE_RETENTION_HOURS),
        )

    def create(self, owner: str = "anonymous") -> Session:
        self._sweep()
        while len(self._items) >= self.max_active:
            victim = self._eviction_candidate()
            if victim is None:
                # Todas atienden un turno: se acepta pasar el cupo antes que perder una en vuelo.
                break
            del self._items[victim.id]
        now = self._now()
        session = Session(
            id=str(uuid.uuid4()),
            history=ConversationHistory(max_turns=get_settings().SESSION_MAX_TURNS),
            owner=owner,
            created_at=now,
            updated_at=now,
        )
        self._items[session.id] = session
        return session

    def get(self, session_id: str, owner: str = "anonymous", *, allow_archived: bool = False) -> Session:
        """La sesión de ``owner``. La de otro usuario responde igual que una inexistente (404).

        Una archivada lanza ``SessionArchivedError`` salvo con ``allow_archived`` (lectura, herencia).
        """
        self._sweep()
        session = self._items.get(session_id)
        if session is None or session.owner != owner:
            raise SessionNotFoundError(session_id)
        if session.status == "archived":
            if not allow_archived:
                raise SessionArchivedError(session_id, session.archived_reason or "archivada")
            return session
        self.touch(session)
        return session

    def archive(self, session: Session, reason: str, *, at: datetime | None = None) -> None:
        if session.status == "archived":
            return
        session.status = "archived"
        session.archived_reason = reason
        session.archived_at = at or self._now()

    def ensure_current(self, session: Session) -> None:
        """Tras esperar el lock: la sesión sigue en el almacén y activa. Si no, el turno no corre."""
        if self._items.get(session.id) is not session:
            raise SessionNotFoundError(session.id)
        if session.status == "archived":
            raise SessionArchivedError(session.id, session.archived_reason or "archivada")

    def touch(self, session: Session) -> None:
        session.updated_at = self._now()

    def _eviction_candidate(self) -> Session | None:
        idle = [item for item in self._items.values() if not item.lock.locked()]
        archived = [item for item in idle if item.status == "archived"]
        pool = archived or idle
        return min(pool, key=lambda item: item.archived_at or item.updated_at) if pool else None

    def _sweep(self) -> None:
        now = self._now()
        for key, item in list(self._items.items()):
            if item.lock.locked():
                continue
            if item.status == "active" and now - item.updated_at > self.idle_archive:
                # Archivada desde que venció el plazo, no desde que alguien la miró.
                self.archive(item, "inactividad", at=item.updated_at + self.idle_archive)
            if item.status == "archived" and item.archived_at and now - item.archived_at > self.archive_retention:
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
