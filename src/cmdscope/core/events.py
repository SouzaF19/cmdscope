"""Typed event and collector-status models shared by every layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TypeAlias

JSONValue: TypeAlias = str | int | float | bool | list["JSONValue"] | dict[str, "JSONValue"] | None


class EventKind(StrEnum):
    """Stable event identifiers persisted in SQLite and exported in reports."""

    FILESYSTEM_CREATED = "filesystem.created"
    FILESYSTEM_MODIFIED = "filesystem.modified"
    FILESYSTEM_DELETED = "filesystem.deleted"
    PROCESS_STARTED = "process.started"
    PROCESS_FINISHED = "process.finished"
    PROCESS_STILL_RUNNING = "process.still_running"
    NETWORK_CONNECTION = "network.connection"
    SESSION_WARNING = "session.warning"


class CollectorState(StrEnum):
    """Outcome of a collector for one session."""

    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"
    DISABLED = "disabled"


class SessionStatus(StrEnum):
    """Lifecycle state of a monitored execution."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


def utc_now() -> datetime:
    """Return an aware UTC timestamp."""

    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Event:
    """An observation emitted by a collector (before it is stored)."""

    kind: EventKind
    occurred_at: datetime
    source: str
    payload: dict[str, JSONValue]

    @classmethod
    def create(
        cls,
        kind: EventKind,
        *,
        source: str,
        payload: dict[str, JSONValue] | None = None,
        occurred_at: datetime | None = None,
    ) -> Event:
        """Build an event with a UTC timestamp and a plain JSON payload."""

        return cls(
            kind=kind,
            occurred_at=occurred_at or utc_now(),
            source=source,
            payload=payload or {},
        )


@dataclass(frozen=True, slots=True)
class StoredEvent:
    """An event loaded from the local database."""

    id: int
    session_id: int
    kind: EventKind
    occurred_at: datetime
    source: str
    payload: dict[str, JSONValue]


@dataclass(frozen=True, slots=True)
class CollectorStatus:
    """Reported coverage and health for a single collector."""

    name: str
    state: CollectorState
    message: str | None = None
    details: dict[str, JSONValue] = field(default_factory=dict)
    started_at: datetime | None = None
    stopped_at: datetime | None = None

    @property
    def healthy(self) -> bool:
        return self.state in (CollectorState.OK, CollectorState.DISABLED)

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "name": self.name,
            "state": str(self.state),
            "message": self.message,
            "details": self.details,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "stopped_at": self.stopped_at.isoformat() if self.stopped_at else None,
        }
