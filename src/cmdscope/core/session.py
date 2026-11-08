"""Session and summary models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from cmdscope.core.events import CollectorStatus, JSONValue, SessionStatus


def session_ref(session_id: int) -> str:
    """Format a database session id for humans (``S000001``)."""

    if session_id <= 0:
        raise ValueError("session id must be positive")
    return f"S{session_id:06d}"


def parse_session_ref(value: str) -> int:
    """Accept ``1``, ``S1``, or ``S000001`` and return the numeric id."""

    text = value.strip()
    if text[:1].lower() == "s":
        text = text[1:]
    if not text.isdigit() or int(text) <= 0:
        raise ValueError(f"invalid session reference: {value!r}")
    return int(text)


@dataclass(frozen=True, slots=True)
class Session:
    """Persisted metadata for one monitored execution."""

    id: int
    status: SessionStatus
    command: tuple[str, ...]
    command_display: str
    cwd: str
    platform_name: str
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    duration_s: float | None
    exit_code: int | None
    interrupted: bool
    error: str | None
    env_diff: dict[str, JSONValue]
    options: dict[str, JSONValue]
    collectors: tuple[CollectorStatus, ...] = ()

    @property
    def ref(self) -> str:
        return session_ref(self.id)


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """Counts derived exclusively from stored events and collector status."""

    session: Session
    events_total: int
    filesystem_created: int
    filesystem_modified: int
    filesystem_deleted: int
    processes_started: int
    processes_finished: int
    processes_still_running: int
    network_connections: int
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class RunResult:
    """Result returned by the session runner."""

    session_id: int
    exit_code: int
    interrupted: bool
    event_count: int
    events_truncated: bool
    summary: SessionSummary
