"""Report rendering tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from cmdscope.analysis.comparison import ComparisonItem, ComparisonResult
from cmdscope.analysis.summary import summarize
from cmdscope.core.events import (
    CollectorState,
    CollectorStatus,
    EventKind,
    StoredEvent,
)
from cmdscope.core.session import Session, SessionStatus
from cmdscope.reports.html import render_html
from cmdscope.reports.json_report import build_report, render_json
from cmdscope.reports.terminal import (
    render_comparison,
    render_session_report,
    render_sessions,
)


def _session() -> Session:
    now = datetime.now(UTC)
    return Session(
        id=1,
        status=SessionStatus.COMPLETED,
        command=("python", "app.py"),
        command_display="python app.py",
        cwd="/tmp/work",
        platform_name="test",
        created_at=now,
        started_at=now,
        ended_at=now,
        duration_s=2.5,
        exit_code=0,
        interrupted=False,
        error=None,
        env_diff={"added": {"MY_TOKEN": "[REDACTED]"}},
        options={"watch_roots": ["/tmp/work"]},
        collectors=(
            CollectorStatus(
                name="filesystem",
                state=CollectorState.OK,
                details={"backend": "native"},
            ),
        ),
    )


def _events() -> list[StoredEvent]:
    now = datetime.now(UTC)
    return [
        StoredEvent(
            id=1,
            session_id=1,
            kind=EventKind.FILESYSTEM_CREATED,
            occurred_at=now,
            source="filesystem",
            payload={"path": "/tmp/work/new.txt", "size": 12, "is_dir": False},
        ),
        StoredEvent(
            id=2,
            session_id=1,
            kind=EventKind.PROCESS_STARTED,
            occurred_at=now,
            source="processes",
            payload={"pid": 42, "name": "python", "command": "python app.py"},
        ),
        StoredEvent(
            id=3,
            session_id=1,
            kind=EventKind.NETWORK_CONNECTION,
            occurred_at=now,
            source="network",
            payload={
                "pid": 42,
                "process_name": "python",
                "protocol": "TCP",
                "local_address": "127.0.0.1:5000",
                "remote_address": "93.184.216.34:443",
                "status": "ESTABLISHED",
            },
        ),
    ]


def test_terminal_report_contains_the_observed_facts() -> None:
    session = _session()
    events = _events()
    summary = summarize(session, events)
    text = render_session_report(session, summary, events, limit=50, color=False)
    assert "CMDSCOPE - EXECUTION REPORT" in text
    assert "python app.py" in text
    assert "Created:" in text
    assert "Connections observed: 1" in text
    assert "new.txt" in text
    assert "\033[" not in text


def test_terminal_report_limits_events() -> None:
    session = _session()
    events = [*_events(), _events()[0]]
    events[-1] = StoredEvent(
        id=4,
        session_id=1,
        kind=EventKind.FILESYSTEM_CREATED,
        occurred_at=events[-1].occurred_at,
        source="filesystem",
        payload={"path": "/tmp/work/second.txt", "size": 3, "is_dir": False},
    )
    summary = summarize(session, events)
    text = render_session_report(session, summary, events, limit=1, color=False)
    assert "more filesystem events" in text


def test_environment_is_hidden_by_default() -> None:
    session = _session()
    events = _events()
    summary = summarize(session, events)
    text = render_session_report(session, summary, events, include_environment=False)
    assert "ENVIRONMENT" not in text
    visible = render_session_report(session, summary, events, include_environment=True)
    assert "ENVIRONMENT" in visible
    assert "MY_TOKEN" in visible


def test_json_report_is_machine_readable() -> None:
    session = _session()
    events = _events()
    summary = summarize(session, events)
    report = build_report(session, summary, events)
    payload = json.loads(render_json(report))
    assert payload["schema"] == "cmdscope.report/v1"
    assert payload["summary"]["filesystem_created"] == 1
    assert payload["summary"]["network_connections"] == 1
    assert len(payload["events"]) == 3
    assert "environment_diff" not in payload["session"]

    with_environment = build_report(session, summary, events, include_environment=True)
    assert "environment_diff" in with_environment["session"]


def test_html_report_escapes_untrusted_values() -> None:
    session = _session()
    now = datetime.now(UTC)
    events = [
        StoredEvent(
            id=1,
            session_id=1,
            kind=EventKind.FILESYSTEM_CREATED,
            occurred_at=now,
            source="filesystem",
            payload={"path": "<script>alert(1)</script>", "size": 1, "is_dir": False},
        )
    ]
    summary = summarize(session, events)
    report = build_report(session, summary, events)
    html = render_html(report)
    assert "<!doctype html>" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert" not in html
    assert "CmdScope" in html


def test_sessions_table_renders_rows_and_empty_state() -> None:
    assert "No sessions recorded yet." in render_sessions([])
    text = render_sessions([_session()])
    assert "S000001" in text
    assert "python app.py" in text


def test_comparison_report_lists_differences() -> None:
    session = _session()
    result = ComparisonResult(
        session_a=session,
        session_b=session,
        only_in_a=(ComparisonItem("filesystem", "created", "/tmp/a"),),
        only_in_b=(ComparisonItem("network", "connection", "curl -> 1.1.1.1:443"),),
        common_count=3,
        events_a=4,
        events_b=4,
    )
    text = render_comparison(result)
    assert "CMDSCOPE - SESSION COMPARISON" in text
    assert "/tmp/a" in text
    assert "curl -> 1.1.1.1:443" in text
    assert "Shared observations: 3" in text
