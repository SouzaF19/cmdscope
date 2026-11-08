"""Shared test helpers (no pytest-specific behavior)."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from cmdscope.core.collector import CollectorContext
from cmdscope.core.events import Event
from cmdscope.core.ignore import IgnoreRules
from cmdscope.core.registry import ProcessRegistry


def make_context(
    root: Path,
    emit: Callable[[Event], None],
    *,
    registry: ProcessRegistry | None = None,
    root_pid: int = 0,
    ignore: IgnoreRules | None = None,
    use_native_events: bool = False,
) -> CollectorContext:
    return CollectorContext(
        session_id=1,
        watch_roots=(root,),
        ignore=ignore or IgnoreRules(),
        root_pid=root_pid,
        command_name="test-command",
        registry=registry if registry is not None else ProcessRegistry(),
        emit=emit,
        filesystem_poll_interval=0.02,
        process_poll_interval=0.02,
        network_poll_interval=0.02,
        max_files=10_000,
        use_native_events=use_native_events,
    )


def wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()
