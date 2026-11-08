"""JSON report builder."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from cmdscope.analysis.comparison import ComparisonResult
from cmdscope.core.events import EventKind, StoredEvent
from cmdscope.core.session import Session, SessionSummary

REPORT_SCHEMA = "cmdscope.report/v1"


def _session_dict(session: Session, *, include_environment: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": session.id,
        "ref": session.ref,
        "status": str(session.status),
        "command": list(session.command),
        "command_display": session.command_display,
        "cwd": session.cwd,
        "platform": session.platform_name,
        "created_at": session.created_at.isoformat(),
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
        "duration_s": session.duration_s,
        "exit_code": session.exit_code,
        "interrupted": session.interrupted,
        "error": session.error,
        "options": session.options,
    }
    if include_environment:
        result["environment_diff"] = session.env_diff
    return result


def _event_dict(event: StoredEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "kind": str(event.kind),
        "occurred_at": event.occurred_at.isoformat(),
        "source": event.source,
        "payload": event.payload,
    }


def build_report(
    session: Session,
    summary: SessionSummary,
    events: Sequence[StoredEvent],
    *,
    include_environment: bool = False,
    event_limit: int | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a stable JSON-friendly report document."""

    selected = list(events if event_limit is None else events[:event_limit])
    return {
        "schema": REPORT_SCHEMA,
        "generated_at": (generated_at or datetime.now(UTC)).isoformat(),
        "session": _session_dict(session, include_environment=include_environment),
        "summary": {
            "events_total": summary.events_total,
            "filesystem_created": summary.filesystem_created,
            "filesystem_modified": summary.filesystem_modified,
            "filesystem_deleted": summary.filesystem_deleted,
            "processes_started": summary.processes_started,
            "processes_finished": summary.processes_finished,
            "processes_still_running": summary.processes_still_running,
            "network_connections": summary.network_connections,
        },
        "collectors": [collector.to_dict() for collector in session.collectors],
        "events": [_event_dict(event) for event in selected],
        "events_shown": len(selected),
        "limitations": [
            "Only changes under the configured watch roots are observed.",
            "Processes and network sockets are sampled; short-lived ones can be missed.",
            "File contents are never captured.",
            "CmdScope is an observability tool, not a sandbox or isolation boundary.",
            "Runtime environment mutations are not observable externally.",
        ],
        "warnings": list(summary.warnings),
    }


def render_json(report: dict[str, Any]) -> str:
    """Serialize a report deterministically for terminals and files."""

    return json.dumps(report, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def build_comparison_report(result: ComparisonResult) -> dict[str, Any]:
    """Build the JSON document used by ``cmdscope compare --format json``."""

    def item_dict(item: Any) -> dict[str, str]:
        return {
            "category": item.category,
            "action": item.action,
            "label": item.label,
        }

    return {
        "schema": "cmdscope.comparison/v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "session_a": {
            "id": result.session_a.id,
            "ref": result.session_a.ref,
            "command": result.session_a.command_display,
            "events_total": result.events_a,
        },
        "session_b": {
            "id": result.session_b.id,
            "ref": result.session_b.ref,
            "command": result.session_b.command_display,
            "events_total": result.events_b,
        },
        "common_count": result.common_count,
        "only_in_a": [item_dict(item) for item in result.only_in_a],
        "only_in_b": [item_dict(item) for item in result.only_in_b],
        "note": "Comparison uses normalized paths and event signatures; timestamps are ignored.",
    }


def render_comparison_json(result: ComparisonResult) -> str:
    return json.dumps(build_comparison_report(result), indent=2, ensure_ascii=False) + "\n"


def is_network_event(event: StoredEvent) -> bool:
    return event.kind == EventKind.NETWORK_CONNECTION
