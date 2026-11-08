"""Plain-text terminal reports."""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import TypeVar

from cmdscope.analysis.comparison import ComparisonItem, ComparisonResult
from cmdscope.core.events import CollectorState, EventKind, StoredEvent
from cmdscope.core.session import Session, SessionSummary

T = TypeVar("T")

RULE = "=" * 70
_BOLD_CYAN = "1;36"
_BOLD = "1"
_GREEN = "32"
_YELLOW = "33"
_RED = "31"
_DIM = "2"


def _style(text: str, code: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"\033[{code}m{text}\033[0m"


def _heading(text: str, color: bool) -> str:
    return _style(text, _BOLD_CYAN, color)


def _fmt_time(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _fmt_duration(value: float | None) -> str:
    if value is None:
        return "-"
    if value < 1:
        return f"{value * 1000:.0f} ms"
    return f"{value:.2f} s"


def _fmt_size(value: object) -> str:
    if not isinstance(value, int):
        return "-"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _short_path(path: str, cwd: str) -> str:
    try:
        relative = os.path.relpath(path, cwd)
    except ValueError:
        relative = path
    if relative.startswith(".."):
        relative = path
    if len(relative) > 110:
        relative = "..." + relative[-107:]
    return relative


def _limited(
    items: Sequence[T],
    limit: int | None,
) -> tuple[list[T], int]:
    if limit is None:
        return list(items), 0
    return list(items[:limit]), max(0, len(items) - limit)


def _status_label(state: CollectorState) -> str:
    return str(state)


def _event_symbol(kind: EventKind) -> str:
    return {
        EventKind.FILESYSTEM_CREATED: "+",
        EventKind.FILESYSTEM_MODIFIED: "~",
        EventKind.FILESYSTEM_DELETED: "-",
    }.get(kind, "?")


def _event_color(kind: EventKind) -> str:
    return {
        EventKind.FILESYSTEM_CREATED: _GREEN,
        EventKind.FILESYSTEM_MODIFIED: _YELLOW,
        EventKind.FILESYSTEM_DELETED: _RED,
    }.get(kind, _DIM)


def _filesystem_lines(
    events: list[StoredEvent],
    *,
    cwd: str,
    limit: int | None,
    color: bool,
) -> list[str]:
    if not events:
        return ["  (no filesystem events observed)"]
    selected, hidden = _limited(events, limit)
    lines = []
    for event in selected:
        payload = event.payload
        path = str(payload.get("path", ""))
        is_dir = bool(payload.get("is_dir", False))
        display = _short_path(path, cwd) + ("/" if is_dir else "")
        size = "" if is_dir else f" ({_fmt_size(payload.get('size'))})"
        symbol = _style(_event_symbol(event.kind), _event_color(event.kind), color)
        lines.append(f"  {symbol} {display}{size}")
    if hidden:
        lines.append(f"  ... {hidden} more filesystem events (use --all to show)")
    return lines


def _process_lines(events: list[StoredEvent], *, limit: int | None) -> list[str]:
    if not events:
        return ["  (no process events observed)"]
    selected, hidden = _limited(events, limit)
    lines = []
    for event in selected:
        payload = event.payload
        pid = payload.get("pid", "?")
        name = payload.get("name", "unknown")
        command = str(payload.get("command", "") or name)
        if len(command) > 100:
            command = command[:97] + "..."
        if event.kind == EventKind.PROCESS_STARTED:
            lines.append(f"  [pid {pid}] started: {command}")
        elif event.kind == EventKind.PROCESS_FINISHED:
            reason = payload.get("reason", "finished")
            lines.append(f"  [pid {pid}] finished: {name} ({reason})")
        else:
            lines.append(f"  [pid {pid}] still running: {command}")
    if hidden:
        lines.append(f"  ... {hidden} more process events (use --all to show)")
    return lines


def _network_lines(events: list[StoredEvent], *, limit: int | None) -> list[str]:
    if not events:
        return ["  (no network endpoints observed)"]
    selected, hidden = _limited(events, limit)
    lines = []
    for event in selected:
        payload = event.payload
        protocol = payload.get("protocol", "TCP")
        status = payload.get("status", "UNKNOWN")
        local = payload.get("local_address") or "-"
        remote = payload.get("remote_address") or "listening"
        process = payload.get("process_name", "unknown")
        pid = payload.get("pid", "?")
        lines.append(f"  [pid {pid}] {process}: {local} -> {remote} ({protocol}, {status})")
    if hidden:
        lines.append(f"  ... {hidden} more network events (use --all to show)")
    return lines


def _environment_lines(session: Session) -> list[str]:
    diff = session.env_diff
    added = diff.get("added")
    removed = diff.get("removed")
    changed = diff.get("changed")
    lines: list[str] = []
    if isinstance(added, dict):
        for added_key in sorted(added):
            lines.append(f"  + {added_key}={_truncate_value(added[added_key])}")
    if isinstance(removed, list):
        for removed_key in removed:
            lines.append(f"  - {removed_key!s}")
    if isinstance(changed, dict):
        for changed_key in sorted(changed):
            detail = changed[changed_key]
            if isinstance(detail, dict):
                before = _truncate_value(detail.get("before"))
                after = _truncate_value(detail.get("after"))
                lines.append(f"  ~ {changed_key}: {before} -> {after}")
            else:
                lines.append(f"  ~ {changed_key}")
    return lines or ["  (no initial environment differences; runtime mutations are not observable)"]


def _truncate_value(value: object, limit: int = 80) -> str:
    text = str(value)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def render_session_report(
    session: Session,
    summary: SessionSummary,
    events: Sequence[StoredEvent],
    *,
    limit: int | None = 50,
    include_environment: bool = False,
    color: bool = False,
) -> str:
    """Render a complete, human-readable report for one session."""

    started = session.started_at or session.created_at
    watch_roots = session.options.get("watch_roots")
    if isinstance(watch_roots, list):
        roots_text = ", ".join(str(root) for root in watch_roots)
    else:
        roots_text = "-"

    lines = [
        _heading("CMDSCOPE - EXECUTION REPORT", color),
        RULE,
        f"Session:      {session.ref}",
        f"Command:      {session.command_display or '(unknown)'}",
        f"Directory:    {session.cwd}",
        f"Platform:     {session.platform_name}",
        f"Started:      {_fmt_time(started)}",
        f"Duration:     {_fmt_duration(session.duration_s)}",
        f"Exit status:  {session.exit_code if session.exit_code is not None else '-'}",
        f"Status:       {session.status}",
    ]
    if session.interrupted:
        lines.append("Interrupted:  yes")
    if session.error:
        lines.append(f"Error:        {session.error}")
    lines.append("")

    lines.append(_heading("FILESYSTEM", color))
    lines.append(f"  Watch roots: {roots_text}")
    lines.append(f"  Created:     {summary.filesystem_created}")
    lines.append(f"  Modified:    {summary.filesystem_modified}")
    lines.append(f"  Deleted:     {summary.filesystem_deleted}")
    filesystem_events = [
        event
        for event in events
        if event.kind
        in {
            EventKind.FILESYSTEM_CREATED,
            EventKind.FILESYSTEM_MODIFIED,
            EventKind.FILESYSTEM_DELETED,
        }
    ]
    lines.extend(_filesystem_lines(filesystem_events, cwd=session.cwd, limit=limit, color=color))
    lines.append("")

    lines.append(_heading("PROCESSES", color))
    lines.append(f"  Started:       {summary.processes_started}")
    lines.append(f"  Finished:      {summary.processes_finished}")
    lines.append(f"  Still running: {summary.processes_still_running}")
    process_events = [
        event
        for event in events
        if event.kind
        in {
            EventKind.PROCESS_STARTED,
            EventKind.PROCESS_FINISHED,
            EventKind.PROCESS_STILL_RUNNING,
        }
    ]
    lines.extend(_process_lines(process_events, limit=limit))
    lines.append("")

    lines.append(_heading("NETWORK", color))
    lines.append(f"  Connections observed: {summary.network_connections}")
    network_events = [event for event in events if event.kind == EventKind.NETWORK_CONNECTION]
    lines.extend(_network_lines(network_events, limit=limit))
    lines.append("")

    lines.append(_heading("COLLECTORS", color))
    if not session.collectors:
        lines.append("  (no collector status recorded)")
    for collector in session.collectors:
        message = f"  {collector.message}" if collector.message else ""
        lines.append(f"  {collector.name:<12} {_status_label(collector.state):<9}{message}")
        details = collector.details
        if details:
            compact = ", ".join(f"{key}={value}" for key, value in sorted(details.items()))
            if len(compact) > 150:
                compact = compact[:147] + "..."
            lines.append(f"    {compact}")
    lines.append("")

    if include_environment:
        lines.append(_heading("ENVIRONMENT (initial child diff, sanitized)", color))
        lines.extend(_environment_lines(session))
        lines.append("")

    lines.append(_heading("SUMMARY", color))
    lines.append(f"  {summary.events_total} events observed in this session.")
    lines.append(
        f"  {summary.filesystem_created + summary.filesystem_modified + summary.filesystem_deleted} "
        "filesystem changes, "
        f"{summary.processes_started} processes started, "
        f"{summary.network_connections} network connections."
    )
    lines.append("")

    lines.append(_heading("LIMITATIONS", color))
    lines.append("  - Only changes under the configured watch roots are observed.")
    lines.append("  - Processes and sockets are sampled; short-lived ones can be missed.")
    lines.append("  - File contents are never captured.")
    lines.append("  - CmdScope observability is not sandboxing or isolation.")
    lines.append("  - Runtime environment mutations are not observable externally.")
    for warning in summary.warnings:
        lines.append(f"  - {warning}")
    lines.append("")
    lines.append("Generated locally by CmdScope. No data was transmitted.")
    return "\n".join(lines)


def render_sessions(sessions: Iterable[Session], *, color: bool = False) -> str:
    """Render a compact session-history table."""

    items = list(sessions)
    if not items:
        return "No sessions recorded yet.\n\nRun one with: cmdscope run -- <command>\n"

    headers = ("ID", "STARTED (UTC)", "DURATION", "EXIT", "STATUS", "COMMAND")
    rows: list[tuple[str, ...]] = []
    for session in items:
        command = session.command_display or "(unknown)"
        if len(command) > 60:
            command = command[:57] + "..."
        rows.append(
            (
                session.ref,
                _fmt_time(session.started_at or session.created_at).replace(" UTC", ""),
                _fmt_duration(session.duration_s),
                str(session.exit_code) if session.exit_code is not None else "-",
                str(session.status),
                command,
            )
        )
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]
    lines = [
        _heading("CMDSCOPE - SESSIONS", color),
        RULE,
        "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)),
        "  ".join("-" * width for width in widths),
    ]
    for row in rows:
        lines.append("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))
    return "\n".join(lines)


def _group_items(items: Sequence[ComparisonItem]) -> dict[tuple[str, str], list[str]]:
    grouped: dict[tuple[str, str], list[str]] = {}
    for item in items:
        grouped.setdefault((item.category, item.action), []).append(item.label)
    return grouped


def _comparison_section(
    title: str,
    items: Sequence[ComparisonItem],
    *,
    limit: int | None,
) -> list[str]:
    lines = [title]
    if not items:
        lines.append("  (none)")
        return lines
    grouped = _group_items(items)
    shown = 0
    for (category, action), labels in sorted(grouped.items()):
        lines.append(f"  {category} {action}: {len(labels)}")
        for label in labels:
            if limit is not None and shown >= limit:
                break
            lines.append(f"    {label}")
            shown += 1
        if limit is not None and shown >= limit:
            break
    if limit is not None and len(items) > shown:
        lines.append(f"  ... {len(items) - shown} more differences (use --all to show)")
    return lines


def render_comparison(
    result: ComparisonResult,
    *,
    limit: int | None = 50,
    color: bool = False,
) -> str:
    """Render differences between two observed sessions."""

    lines = [
        _heading("CMDSCOPE - SESSION COMPARISON", color),
        RULE,
        f"A: {result.session_a.ref}  {result.session_a.command_display}",
        f"   {result.events_a} events observed",
        f"B: {result.session_b.ref}  {result.session_b.command_display}",
        f"   {result.events_b} events observed",
        "",
        _heading("SUMMARY", color),
        f"  Shared observations: {result.common_count}",
        f"  Only in A:          {len(result.only_in_a)}",
        f"  Only in B:          {len(result.only_in_b)}",
        "",
    ]
    lines.extend(_comparison_section("ONLY IN A", result.only_in_a, limit=limit))
    lines.append("")
    lines.extend(_comparison_section("ONLY IN B", result.only_in_b, limit=limit))
    lines.append("")
    if not result.only_in_a and not result.only_in_b:
        lines.append("No differences in observed events.")
        lines.append("")
    lines.append("Comparison uses normalized paths and event signatures; timestamps are ignored.")
    return "\n".join(lines)


def category_counts(events: Sequence[StoredEvent]) -> Counter[str]:
    """Small helper used by reports/tests to summarize categories."""

    counts: Counter[str] = Counter()
    for event in events:
        counts[str(event.kind).split(".", 1)[0]] += 1
    return counts
