"""Summary calculations derived only from stored events."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from cmdscope.core.events import CollectorState, EventKind, StoredEvent
from cmdscope.core.session import Session, SessionSummary


def summarize(session: Session, events: Sequence[StoredEvent]) -> SessionSummary:
    """Count observed events and surface collector degradation as warnings."""

    counts = Counter(event.kind for event in events)
    warnings: list[str] = []
    for collector in session.collectors:
        if collector.state in (CollectorState.DEGRADED, CollectorState.FAILED):
            message = collector.message or f"collector state is {collector.state}"
            warnings.append(f"{collector.name}: {message}")
    if session.error:
        warnings.append(session.error)
    if not events:
        warnings.append("no events were recorded for this session")

    return SessionSummary(
        session=session,
        events_total=len(events),
        filesystem_created=counts[EventKind.FILESYSTEM_CREATED],
        filesystem_modified=counts[EventKind.FILESYSTEM_MODIFIED],
        filesystem_deleted=counts[EventKind.FILESYSTEM_DELETED],
        processes_started=counts[EventKind.PROCESS_STARTED],
        processes_finished=counts[EventKind.PROCESS_FINISHED],
        processes_still_running=counts[EventKind.PROCESS_STILL_RUNNING],
        network_connections=counts[EventKind.NETWORK_CONNECTION],
        warnings=tuple(warnings),
    )
