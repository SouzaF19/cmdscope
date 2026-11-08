"""Collector interface and the immutable context passed to collectors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from cmdscope.core.events import CollectorStatus, Event
from cmdscope.core.ignore import IgnoreRules
from cmdscope.core.registry import ProcessRegistry


@dataclass(frozen=True, slots=True)
class CollectorContext:
    """Everything a collector needs to observe one session.

    Collectors must use ``emit`` for observations and must never write directly
    to storage, reports, or the terminal.
    """

    session_id: int
    watch_roots: tuple[Path, ...]
    ignore: IgnoreRules
    root_pid: int
    command_name: str
    registry: ProcessRegistry
    emit: Callable[[Event], None]
    filesystem_poll_interval: float = 1.0
    process_poll_interval: float = 0.5
    network_poll_interval: float = 0.25
    max_files: int = 100_000
    use_native_events: bool = True
    started_at: datetime | None = None


class Collector(Protocol):
    """Minimal lifecycle implemented by every collector."""

    name: str

    def start(self, context: CollectorContext) -> None:
        """Begin observing. Must perform the baseline before the command starts."""

    def stop(self) -> CollectorStatus:
        """Stop observing, reconcile, and return an honest status."""
