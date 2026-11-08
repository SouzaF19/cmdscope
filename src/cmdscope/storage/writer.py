"""Event writer thread with bounded session storage."""

from __future__ import annotations

import queue
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from cmdscope.core.events import Event
from cmdscope.errors import StorageError
from cmdscope.storage.sqlite import _iso, _json


@dataclass(frozen=True, slots=True)
class EventWriterStats:
    written: int
    dropped: int
    truncated: bool


class EventWriter:
    """Serialize collector events into SQLite without blocking collectors.

    A single writer thread owns its SQLite connection. Collectors only enqueue
    typed events, so filesystem observers, process pollers, and network pollers
    never contend for the database or for each other's locks.
    """

    def __init__(self, database: Path, session_id: int, max_events: int) -> None:
        if max_events < 1:
            raise ValueError("max_events must be at least 1")
        self._database = Path(database)
        self._session_id = session_id
        self._max_events = max_events
        self._queue: queue.Queue[Event | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="cmdscope-event-writer", daemon=True)
        self._closed = False
        self._written = 0
        self._dropped = 0
        self._error: BaseException | None = None

    def start(self) -> None:
        self._thread.start()

    def emit(self, event: Event) -> None:
        if self._closed:
            return
        self._queue.put(event)

    def close(self) -> EventWriterStats:
        if not self._closed:
            self._closed = True
            self._queue.put(None)
        self._thread.join(timeout=30)
        if self._thread.is_alive():
            raise StorageError("event writer did not finish within 30 seconds")
        if self._error is not None:
            raise StorageError(f"event writer failed: {self._error}") from self._error
        return EventWriterStats(
            written=self._written,
            dropped=self._dropped,
            truncated=self._dropped > 0,
        )

    def _run(self) -> None:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self._database, timeout=30)
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("PRAGMA foreign_keys = ON")
            while True:
                try:
                    event = self._queue.get(timeout=0.5)
                except queue.Empty:
                    connection.commit()
                    continue
                if event is None:
                    break
                if self._written >= self._max_events:
                    self._dropped += 1
                    continue
                connection.execute(
                    """
                    INSERT INTO events(session_id, kind, occurred_at, source, payload_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        self._session_id,
                        str(event.kind),
                        _iso(event.occurred_at),
                        event.source,
                        _json(event.payload),
                    ),
                )
                self._written += 1
                if self._written % 128 == 0:
                    connection.commit()
            connection.commit()
        except BaseException as exc:
            self._error = exc
        finally:
            if connection is not None:
                connection.close()
