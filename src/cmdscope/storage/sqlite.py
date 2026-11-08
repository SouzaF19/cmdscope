"""SQLite persistence for sessions, events, and collector status."""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from cmdscope.core.events import (
    CollectorState,
    CollectorStatus,
    Event,
    EventKind,
    JSONValue,
    SessionStatus,
    StoredEvent,
)
from cmdscope.core.session import Session, parse_session_ref
from cmdscope.errors import SessionNotFoundError, StorageError

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL,
    command_json TEXT NOT NULL,
    command_display TEXT NOT NULL,
    cwd TEXT NOT NULL,
    platform_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    duration_s REAL,
    exit_code INTEGER,
    interrupted INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    env_diff_json TEXT NOT NULL DEFAULT '{}',
    options_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    source TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_session_kind
    ON events(session_id, kind);

CREATE TABLE IF NOT EXISTS collectors (
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    state TEXT NOT NULL,
    message TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT,
    stopped_at TEXT,
    PRIMARY KEY (session_id, name)
);
"""


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load_json(value: str) -> JSONValue:
    try:
        loaded: JSONValue = json.loads(value)
    except json.JSONDecodeError as exc:
        raise StorageError(f"corrupt JSON stored in the database: {exc}") from exc
    return loaded


class Storage:
    """Small SQLite repository with schema versioning and cascading deletes."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(
                f"cannot create database directory {self.path.parent}: {exc}"
            ) from exc

        try:
            self._connection = sqlite3.connect(self.path, timeout=30)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 30000")
            with contextlib.suppress(sqlite3.DatabaseError):
                self._connection.execute("PRAGMA journal_mode = WAL")
            self._initialize()
            self._restrict_permissions()
        except sqlite3.Error as exc:
            raise StorageError(f"cannot open database {self.path}: {exc}") from exc

    def _initialize(self) -> None:
        with self._connection:
            self._connection.executescript(_SCHEMA)
            row = self._connection.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                self._connection.execute(
                    "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
                return
            stored = int(row["value"])
            if stored > SCHEMA_VERSION:
                raise StorageError(
                    f"database schema {stored} is newer than this CmdScope build "
                    f"({SCHEMA_VERSION}); upgrade CmdScope"
                )

    def _restrict_permissions(self) -> None:
        if os.name == "nt":
            return
        try:
            os.chmod(self.path, 0o600)
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass

    def close(self) -> None:
        with contextlib.suppress(sqlite3.Error):
            self._connection.close()

    def __enter__(self) -> Storage:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def schema_version(self) -> int:
        row = self._connection.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        return int(row["value"]) if row else 0

    def quick_check(self) -> str:
        row = self._connection.execute("PRAGMA quick_check").fetchone()
        return str(row[0]) if row else "unknown"

    def create_session(
        self,
        *,
        command: Sequence[str],
        command_display: str,
        cwd: str,
        platform_name: str,
        created_at: datetime,
        env_diff: dict[str, JSONValue],
        options: dict[str, JSONValue],
    ) -> int:
        try:
            with self._connection:
                cursor = self._connection.execute(
                    """
                    INSERT INTO sessions(
                        status, command_json, command_display, cwd, platform_name,
                        created_at, env_diff_json, options_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(SessionStatus.RUNNING),
                        _json(list(command)),
                        command_display,
                        cwd,
                        platform_name,
                        _iso(created_at),
                        _json(env_diff),
                        _json(options),
                    ),
                )
                rowid = cursor.lastrowid
                if rowid is None:
                    raise StorageError("SQLite did not return a row id")
                return int(rowid)
        except sqlite3.Error as exc:
            raise StorageError(f"cannot create session: {exc}") from exc

    def mark_command_started(self, session_id: int, started_at: datetime) -> None:
        self._execute(
            "UPDATE sessions SET started_at = ? WHERE id = ?",
            (_iso(started_at), session_id),
        )

    def finish_session(
        self,
        session_id: int,
        *,
        status: SessionStatus,
        ended_at: datetime,
        duration_s: float,
        exit_code: int | None,
        interrupted: bool,
        error: str | None,
    ) -> None:
        self._execute(
            """
            UPDATE sessions
               SET status = ?, ended_at = ?, duration_s = ?, exit_code = ?,
                   interrupted = ?, error = ?
             WHERE id = ?
            """,
            (
                str(status),
                _iso(ended_at),
                duration_s,
                exit_code,
                1 if interrupted else 0,
                error,
                session_id,
            ),
        )

    def set_collector_statuses(self, session_id: int, statuses: Iterable[CollectorStatus]) -> None:
        try:
            with self._connection:
                self._connection.execute(
                    "DELETE FROM collectors WHERE session_id = ?", (session_id,)
                )
                self._connection.executemany(
                    """
                    INSERT INTO collectors(
                        session_id, name, state, message, details_json,
                        started_at, stopped_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            session_id,
                            status.name,
                            str(status.state),
                            status.message,
                            _json(status.details),
                            _iso(status.started_at) if status.started_at else None,
                            _iso(status.stopped_at) if status.stopped_at else None,
                        )
                        for status in statuses
                    ],
                )
        except sqlite3.Error as exc:
            raise StorageError(f"cannot store collector status: {exc}") from exc

    def insert_event(self, session_id: int, event: Event) -> int:
        try:
            with self._connection:
                cursor = self._connection.execute(
                    """
                    INSERT INTO events(session_id, kind, occurred_at, source, payload_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        str(event.kind),
                        _iso(event.occurred_at),
                        event.source,
                        _json(event.payload),
                    ),
                )
                rowid = cursor.lastrowid
                if rowid is None:
                    raise StorageError("SQLite did not return a row id")
                return int(rowid)
        except sqlite3.Error as exc:
            raise StorageError(f"cannot store event: {exc}") from exc

    def get_session(self, session_id: int) -> Session | None:
        if session_id <= 0:
            return None
        row = self._connection.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_session(row, self.get_collector_statuses(session_id))

    def require_session(self, reference: str) -> Session:
        try:
            session_id = parse_session_ref(reference)
        except ValueError as exc:
            raise SessionNotFoundError(f"invalid session reference: {reference!r}") from exc
        session = self.get_session(session_id)
        if session is None:
            raise SessionNotFoundError(f"session not found: {reference}")
        return session

    def get_collector_statuses(self, session_id: int) -> tuple[CollectorStatus, ...]:
        rows = self._connection.execute(
            "SELECT * FROM collectors WHERE session_id = ? ORDER BY name",
            (session_id,),
        ).fetchall()
        statuses: list[CollectorStatus] = []
        for row in rows:
            try:
                state = CollectorState(row["state"])
            except ValueError:
                state = CollectorState.DEGRADED
            details = _load_json(row["details_json"])
            statuses.append(
                CollectorStatus(
                    name=str(row["name"]),
                    state=state,
                    message=str(row["message"]) if row["message"] is not None else None,
                    details=details if isinstance(details, dict) else {},
                    started_at=_datetime(row["started_at"]),
                    stopped_at=_datetime(row["stopped_at"]),
                )
            )
        return tuple(statuses)

    def get_events(
        self,
        session_id: int,
        kinds: Sequence[EventKind] | None = None,
    ) -> list[StoredEvent]:
        sql = "SELECT * FROM events WHERE session_id = ?"
        parameters: list[object] = [session_id]
        if kinds:
            placeholders = ", ".join("?" for _ in kinds)
            sql += f" AND kind IN ({placeholders})"
            parameters.extend(str(kind) for kind in kinds)
        sql += " ORDER BY id"
        rows = self._connection.execute(sql, parameters).fetchall()
        events: list[StoredEvent] = []
        for row in rows:
            payload = _load_json(row["payload_json"])
            if not isinstance(payload, dict):
                raise StorageError(f"event {row['id']} has a non-object payload")
            events.append(
                StoredEvent(
                    id=int(row["id"]),
                    session_id=int(row["session_id"]),
                    kind=EventKind(row["kind"]),
                    occurred_at=datetime.fromisoformat(row["occurred_at"]),
                    source=str(row["source"]),
                    payload=payload,
                )
            )
        return events

    def count_events(self, session_id: int) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS total FROM events WHERE session_id = ?", (session_id,)
        ).fetchone()
        return int(row["total"]) if row else 0

    def list_sessions(self, limit: int = 50) -> list[Session]:
        if limit < 1:
            raise ValueError("limit must be positive")
        rows = self._connection.execute(
            "SELECT * FROM sessions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_session(row, ()) for row in rows]

    def delete_session(self, session_id: int) -> bool:
        try:
            with self._connection:
                cursor = self._connection.execute(
                    "DELETE FROM sessions WHERE id = ?", (session_id,)
                )
                return cursor.rowcount > 0
        except sqlite3.Error as exc:
            raise StorageError(f"cannot delete session: {exc}") from exc

    def prune(self, max_sessions: int) -> int:
        """Delete oldest finished sessions when the configured limit is exceeded."""

        if max_sessions <= 0:
            return 0
        row = self._connection.execute(
            "SELECT COUNT(*) AS total FROM sessions WHERE status != ?",
            (str(SessionStatus.RUNNING),),
        ).fetchone()
        total = int(row["total"]) if row else 0
        excess = total - max_sessions
        if excess <= 0:
            return 0
        try:
            with self._connection:
                cursor = self._connection.execute(
                    """
                    DELETE FROM sessions
                     WHERE id IN (
                        SELECT id FROM sessions
                         WHERE status != ?
                         ORDER BY id ASC
                         LIMIT ?
                     )
                    """,
                    (str(SessionStatus.RUNNING), excess),
                )
                return cursor.rowcount
        except sqlite3.Error as exc:
            raise StorageError(f"cannot prune sessions: {exc}") from exc

    def storage_size_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{self.path}{suffix}")
            with contextlib.suppress(OSError):
                total += candidate.stat().st_size
        return total

    def _execute(self, sql: str, parameters: Sequence[object]) -> None:
        try:
            with self._connection:
                self._connection.execute(sql, parameters)
        except sqlite3.Error as exc:
            raise StorageError(f"database update failed: {exc}") from exc

    @staticmethod
    def _row_to_session(row: sqlite3.Row, collectors: tuple[CollectorStatus, ...]) -> Session:
        try:
            command_value = _load_json(row["command_json"])
            env_diff = _load_json(row["env_diff_json"])
            options = _load_json(row["options_json"])
            status = SessionStatus(row["status"])
        except ValueError as exc:
            raise StorageError(f"corrupt session row {row['id']}: {exc}") from exc
        if not isinstance(command_value, list) or not all(
            isinstance(item, str) for item in command_value
        ):
            raise StorageError(f"corrupt command in session {row['id']}")
        return Session(
            id=int(row["id"]),
            status=status,
            command=tuple(str(item) for item in command_value),
            command_display=str(row["command_display"]),
            cwd=str(row["cwd"]),
            platform_name=str(row["platform_name"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=_datetime(row["started_at"]),
            ended_at=_datetime(row["ended_at"]),
            duration_s=float(row["duration_s"]) if row["duration_s"] is not None else None,
            exit_code=int(row["exit_code"]) if row["exit_code"] is not None else None,
            interrupted=bool(row["interrupted"]),
            error=str(row["error"]) if row["error"] is not None else None,
            env_diff=env_diff if isinstance(env_diff, dict) else {},
            options=options if isinstance(options, dict) else {},
            collectors=collectors,
        )
