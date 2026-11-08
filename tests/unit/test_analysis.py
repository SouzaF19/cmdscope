"""Summary and comparison tests."""

from __future__ import annotations

from datetime import UTC, datetime

from cmdscope.analysis.comparison import compare_events
from cmdscope.analysis.summary import summarize
from cmdscope.core.events import (
    CollectorState,
    CollectorStatus,
    EventKind,
    StoredEvent,
)
from cmdscope.core.session import Session, SessionStatus


def _session(session_id: int = 1) -> Session:
    now = datetime.now(UTC)
    return Session(
        id=session_id,
        status=SessionStatus.COMPLETED,
        command=("python", "app.py"),
        command_display="python app.py",
        cwd="/tmp",
        platform_name="test",
        created_at=now,
        started_at=now,
        ended_at=now,
        duration_s=1.0,
        exit_code=0,
        interrupted=False,
        error=None,
        env_diff={},
        options={},
        collectors=(
            CollectorStatus(
                name="filesystem",
                state=CollectorState.DEGRADED,
                message="partial coverage",
            ),
        ),
    )


def _stored(
    event_id: int,
    kind: EventKind,
    payload: dict[str, object],
    session_id: int = 1,
) -> StoredEvent:
    return StoredEvent(
        id=event_id,
        session_id=session_id,
        kind=kind,
        occurred_at=datetime.now(UTC),
        source="test",
        payload=payload,  # type: ignore[arg-type]
    )


def test_summary_counts_events_and_warnings() -> None:
    session = _session()
    events = [
        _stored(1, EventKind.FILESYSTEM_CREATED, {"path": "/tmp/a"}),
        _stored(2, EventKind.FILESYSTEM_MODIFIED, {"path": "/tmp/b"}),
        _stored(3, EventKind.FILESYSTEM_DELETED, {"path": "/tmp/c"}),
        _stored(4, EventKind.PROCESS_STARTED, {"pid": 1, "name": "python"}),
        _stored(5, EventKind.PROCESS_FINISHED, {"pid": 1, "name": "python"}),
        _stored(6, EventKind.PROCESS_STILL_RUNNING, {"pid": 2, "name": "node"}),
        _stored(7, EventKind.NETWORK_CONNECTION, {"pid": 1, "process_name": "python"}),
    ]
    summary = summarize(session, events)
    assert summary.events_total == 7
    assert summary.filesystem_created == 1
    assert summary.filesystem_modified == 1
    assert summary.filesystem_deleted == 1
    assert summary.processes_started == 1
    assert summary.processes_finished == 1
    assert summary.processes_still_running == 1
    assert summary.network_connections == 1
    assert any("filesystem" in warning for warning in summary.warnings)


def test_comparison_reports_unique_observations() -> None:
    session_a = _session(1)
    session_b = _session(2)
    events_a = [
        _stored(1, EventKind.FILESYSTEM_CREATED, {"path": "/tmp/shared"}),
        _stored(2, EventKind.FILESYSTEM_CREATED, {"path": "/tmp/only-a"}),
    ]
    events_b = [
        _stored(3, EventKind.FILESYSTEM_CREATED, {"path": "/tmp/shared"}, session_id=2),
        _stored(4, EventKind.NETWORK_CONNECTION, {"pid": 1, "process_name": "curl"}, session_id=2),
    ]
    result = compare_events(session_a, events_a, session_b, events_b)
    assert result.common_count == 1
    assert [item.label for item in result.only_in_a] == ["/tmp/only-a"]
    assert [item.label for item in result.only_in_b] == ["curl -> unknown (TCP)"]
    assert result.events_a == 2
    assert result.events_b == 2


def test_comparison_of_identical_sessions_has_no_differences() -> None:
    session = _session()
    events = [_stored(1, EventKind.FILESYSTEM_CREATED, {"path": "/tmp/file"})]
    result = compare_events(session, events, session, list(events))
    assert result.only_in_a == ()
    assert result.only_in_b == ()
    assert result.common_count == 1
