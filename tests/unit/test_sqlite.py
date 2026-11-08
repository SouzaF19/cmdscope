"""SQLite persistence and event-writer tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from cmdscope.core.events import (
    CollectorState,
    CollectorStatus,
    Event,
    EventKind,
    SessionStatus,
)
from cmdscope.core.session import parse_session_ref, session_ref
from cmdscope.errors import SessionNotFoundError
from cmdscope.storage.sqlite import Storage
from cmdscope.storage.writer import EventWriter


def _event(kind: EventKind = EventKind.FILESYSTEM_CREATED, path: str = "file.txt") -> Event:
    return Event.create(
        kind,
        source="test",
        payload={"path": path, "size": 10, "is_dir": False},
    )


def test_session_lifecycle_round_trip(tmp_path: Path) -> None:
    database = tmp_path / "nested" / "sessions.db"
    with Storage(database) as storage:
        assert database.exists()
        assert storage.schema_version() == 1
        assert storage.quick_check() == "ok"

        session_id = storage.create_session(
            command=("python", "app.py"),
            command_display="python app.py",
            cwd=str(tmp_path),
            platform_name="test",
            created_at=datetime.now(UTC),
            env_diff={"added": {"KEY": "value"}},
            options={"watch_roots": [str(tmp_path)]},
        )
        assert session_id == 1
        storage.mark_command_started(session_id, datetime.now(UTC))
        storage.insert_event(session_id, _event())
        storage.set_collector_statuses(
            session_id,
            [
                CollectorStatus(
                    name="filesystem",
                    state=CollectorState.OK,
                    details={"backend": "native"},
                )
            ],
        )
        storage.finish_session(
            session_id,
            status=SessionStatus.COMPLETED,
            ended_at=datetime.now(UTC),
            duration_s=1.5,
            exit_code=0,
            interrupted=False,
            error=None,
        )

        session = storage.require_session("S000001")
        assert session.status == SessionStatus.COMPLETED
        assert session.exit_code == 0
        assert session.duration_s == pytest.approx(1.5)
        assert session.command == ("python", "app.py")
        assert session.env_diff == {"added": {"KEY": "value"}}
        assert session.collectors[0].state == CollectorState.OK
        assert storage.count_events(session_id) == 1

        events = storage.get_events(session_id)
        assert len(events) == 1
        assert events[0].payload["path"] == "file.txt"

        assert storage.get_events(session_id, kinds=[EventKind.NETWORK_CONNECTION]) == []
        assert [item.id for item in storage.list_sessions()] == [1]
        assert storage.storage_size_bytes() > 0


def test_invalid_session_reference_raises(tmp_path: Path) -> None:
    with Storage(tmp_path / "sessions.db") as storage:
        with pytest.raises(SessionNotFoundError):
            storage.require_session("not-a-session")
        with pytest.raises(SessionNotFoundError):
            storage.require_session("999")


def test_session_reference_helpers() -> None:
    assert session_ref(1) == "S000001"
    assert parse_session_ref("1") == 1
    assert parse_session_ref("S1") == 1
    assert parse_session_ref("s000042") == 42
    with pytest.raises(ValueError):
        parse_session_ref("0")
    with pytest.raises(ValueError):
        parse_session_ref("abc")


def test_delete_session_removes_events(tmp_path: Path) -> None:
    with Storage(tmp_path / "sessions.db") as storage:
        session_id = storage.create_session(
            command=("cmd",),
            command_display="cmd",
            cwd=str(tmp_path),
            platform_name="test",
            created_at=datetime.now(UTC),
            env_diff={},
            options={},
        )
        storage.insert_event(session_id, _event())
        assert storage.delete_session(session_id) is True
        assert storage.count_events(session_id) == 0
        assert storage.get_session(session_id) is None


def test_prune_keeps_the_newest_sessions(tmp_path: Path) -> None:
    with Storage(tmp_path / "sessions.db") as storage:
        for _ in range(3):
            session_id = storage.create_session(
                command=("cmd",),
                command_display="cmd",
                cwd=str(tmp_path),
                platform_name="test",
                created_at=datetime.now(UTC),
                env_diff={},
                options={},
            )
            storage.finish_session(
                session_id,
                status=SessionStatus.COMPLETED,
                ended_at=datetime.now(UTC),
                duration_s=0.1,
                exit_code=0,
                interrupted=False,
                error=None,
            )
        removed = storage.prune(1)
        assert removed == 2
        assert len(storage.list_sessions()) == 1
        assert storage.prune(0) == 0


def test_event_writer_persists_events(tmp_path: Path) -> None:
    database = tmp_path / "sessions.db"
    with Storage(database) as storage:
        session_id = storage.create_session(
            command=("cmd",),
            command_display="cmd",
            cwd=str(tmp_path),
            platform_name="test",
            created_at=datetime.now(UTC),
            env_diff={},
            options={},
        )
        writer = EventWriter(database, session_id, max_events=10)
        writer.start()
        for index in range(5):
            writer.emit(_event(path=f"file-{index}.txt"))
        stats = writer.close()
        assert stats.written == 5
        assert stats.dropped == 0
        assert stats.truncated is False
        assert storage.count_events(session_id) == 5


def test_event_writer_enforces_the_limit(tmp_path: Path) -> None:
    database = tmp_path / "sessions.db"
    with Storage(database) as storage:
        session_id = storage.create_session(
            command=("cmd",),
            command_display="cmd",
            cwd=str(tmp_path),
            platform_name="test",
            created_at=datetime.now(UTC),
            env_diff={},
            options={},
        )
        writer = EventWriter(database, session_id, max_events=2)
        writer.start()
        for index in range(6):
            writer.emit(_event(path=f"file-{index}.txt"))
        stats = writer.close()
        assert stats.written == 2
        assert stats.dropped == 4
        assert stats.truncated is True
        assert storage.count_events(session_id) == 2
