"""Compare the observed event sets of two sessions."""

from __future__ import annotations

import os
from dataclasses import dataclass

from cmdscope.core.events import EventKind, StoredEvent
from cmdscope.core.session import Session
from cmdscope.storage.sqlite import Storage


@dataclass(frozen=True, slots=True)
class ComparisonItem:
    category: str
    action: str
    label: str


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    session_a: Session
    session_b: Session
    only_in_a: tuple[ComparisonItem, ...]
    only_in_b: tuple[ComparisonItem, ...]
    common_count: int
    events_a: int
    events_b: int


def _filesystem_item(event: StoredEvent) -> ComparisonItem:
    path = str(event.payload.get("path", ""))
    suffix = str(event.kind).split(".", 1)[-1]
    return ComparisonItem(category="filesystem", action=suffix, label=path)


def _process_item(event: StoredEvent) -> ComparisonItem:
    name = str(event.payload.get("name", "unknown"))
    command = str(event.payload.get("command", "")) or name
    action = str(event.kind).split(".", 1)[-1]
    return ComparisonItem(category="processes", action=action, label=command)


def _network_item(event: StoredEvent) -> ComparisonItem:
    process = str(event.payload.get("process_name", "unknown"))
    remote = event.payload.get("remote_address")
    local = event.payload.get("local_address")
    protocol = str(event.payload.get("protocol", "TCP"))
    endpoint = str(remote or local or "unknown")
    return ComparisonItem(
        category="network",
        action="connection",
        label=f"{process} -> {endpoint} ({protocol})",
    )


def _key(event: StoredEvent) -> tuple[str, str] | None:
    if event.kind in {
        EventKind.FILESYSTEM_CREATED,
        EventKind.FILESYSTEM_MODIFIED,
        EventKind.FILESYSTEM_DELETED,
    }:
        path = os.path.normcase(str(event.payload.get("path", "")))
        return (f"filesystem|{event.kind}", path)
    if event.kind in {EventKind.PROCESS_STARTED, EventKind.PROCESS_STILL_RUNNING}:
        name = str(event.payload.get("name", "unknown"))
        command = str(event.payload.get("command", ""))
        return (f"processes|{event.kind}", os.path.normcase(f"{name}|{command}"))
    if event.kind == EventKind.NETWORK_CONNECTION:
        process = str(event.payload.get("process_name", "unknown"))
        remote = str(event.payload.get("remote_address") or "")
        local = str(event.payload.get("local_address") or "")
        protocol = str(event.payload.get("protocol", ""))
        return ("network|connection", os.path.normcase(f"{process}|{protocol}|{remote or local}"))
    return None


def _collect(events: list[StoredEvent]) -> dict[tuple[str, str], ComparisonItem]:
    items: dict[tuple[str, str], ComparisonItem] = {}
    for event in events:
        key = _key(event)
        if key is None or key in items:
            continue
        if key[0].startswith("filesystem"):
            items[key] = _filesystem_item(event)
        elif key[0].startswith("processes"):
            items[key] = _process_item(event)
        else:
            items[key] = _network_item(event)
    return items


def _sorted(items: list[ComparisonItem]) -> tuple[ComparisonItem, ...]:
    return tuple(
        sorted(items, key=lambda item: (item.category, item.action, item.label.casefold()))
    )


def compare_events(
    session_a: Session,
    events_a: list[StoredEvent],
    session_b: Session,
    events_b: list[StoredEvent],
) -> ComparisonResult:
    """Return events unique to each session and the number shared by both."""

    items_a = _collect(events_a)
    items_b = _collect(events_b)
    keys_a = set(items_a)
    keys_b = set(items_b)
    only_a = _sorted([items_a[key] for key in keys_a - keys_b])
    only_b = _sorted([items_b[key] for key in keys_b - keys_a])
    return ComparisonResult(
        session_a=session_a,
        session_b=session_b,
        only_in_a=only_a,
        only_in_b=only_b,
        common_count=len(keys_a & keys_b),
        events_a=len(events_a),
        events_b=len(events_b),
    )


def compare_sessions(storage: Storage, reference_a: str, reference_b: str) -> ComparisonResult:
    """Load both sessions and compare their persisted events."""

    session_a = storage.require_session(reference_a)
    session_b = storage.require_session(reference_b)
    return compare_events(
        session_a,
        storage.get_events(session_a.id),
        session_b,
        storage.get_events(session_b.id),
    )
