"""Filesystem collector: snapshot diff plus native events with polling fallback."""

from __future__ import annotations

import os
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from cmdscope.core.collector import CollectorContext
from cmdscope.core.events import (
    CollectorState,
    CollectorStatus,
    Event,
    EventKind,
    utc_now,
)
from cmdscope.core.ignore import IgnoreRules


@dataclass(frozen=True, slots=True)
class FileState:
    size: int
    mtime_ns: int
    is_dir: bool


@dataclass(frozen=True, slots=True)
class ScanResult:
    states: dict[Path, FileState]
    truncated: bool
    error_count: int
    error_sample: str | None


def _state(path: Path) -> FileState:
    info = path.lstat()
    return FileState(
        size=info.st_size,
        mtime_ns=info.st_mtime_ns,
        is_dir=stat.S_ISDIR(info.st_mode),
    )


def scan_tree(root: Path, ignore: IgnoreRules, max_files: int) -> ScanResult:
    """Walk *root* without following symlinks and return metadata per entry."""

    states: dict[Path, FileState] = {}
    stack = [root]
    error_count = 0
    error_sample: str | None = None
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    if ignore.matches(path, root):
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        error_count += 1
                        error_sample = error_sample or f"{entry.path}: {exc}"
                        continue
                    is_dir = stat.S_ISDIR(info.st_mode)
                    states[path] = FileState(info.st_size, info.st_mtime_ns, is_dir)
                    if len(states) >= max_files:
                        return ScanResult(states, True, error_count, error_sample)
                    if is_dir:
                        stack.append(path)
        except OSError as exc:
            error_count += 1
            error_sample = error_sample or f"{current}: {exc}"
    return ScanResult(states, False, error_count, error_sample)


class FilesystemCollector:
    """Observe metadata changes under explicit roots.

    The baseline snapshot is taken before the monitored command starts. Native
    events provide low latency; a final snapshot reconciliation catches changes
    that the OS event stream missed. If the snapshot limit is reached, CmdScope
    refuses to infer deletions from the partial snapshot and reports the
    collector as degraded.
    """

    name = "filesystem"

    def __init__(
        self,
        *,
        max_files: int,
        poll_interval: float,
        use_native_events: bool = True,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._max_files = max_files
        self._poll_interval = poll_interval
        self._use_native_events = use_native_events
        self._clock = clock
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._states: dict[Path, FileState] = {}
        self._roots: tuple[Path, ...] = ()
        self._ignore = IgnoreRules()
        self._emit: Callable[[Event], None] | None = None
        self._events_emitted = 0
        self._baseline_truncated = False
        self._scan_errors = 0
        self._scan_error_sample: str | None = None
        self._observer: Any = None
        self._thread: threading.Thread | None = None
        self._backend = "none"
        self._observer_error: str | None = None
        self._fatal_error: str | None = None
        self._started_at: datetime | None = None

    def start(self, context: CollectorContext) -> None:
        self._roots = context.watch_roots
        self._ignore = context.ignore
        self._emit = context.emit
        self._started_at = utc_now()

        baseline: dict[Path, FileState] = {}
        for root in self._roots:
            scan = scan_tree(root, self._ignore, self._max_files)
            baseline.update(scan.states)
            self._baseline_truncated = self._baseline_truncated or scan.truncated
            self._accumulate_scan_errors(scan)
        with self._lock:
            self._states = baseline

        if self._use_native_events and self._start_observer():
            self._backend = "native"
        else:
            self._backend = "polling"
            self._thread = threading.Thread(
                target=self._poll_loop, name="cmdscope-fs-poller", daemon=True
            )
            self._thread.start()

    def _accumulate_scan_errors(self, scan: ScanResult) -> None:
        self._scan_errors += scan.error_count
        if self._scan_error_sample is None:
            self._scan_error_sample = scan.error_sample

    def _start_observer(self) -> bool:
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except Exception as exc:  # pragma: no cover - required dependency is installed
            self._observer_error = f"watchdog unavailable: {exc}"
            return False

        collection = self

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event: Any) -> None:
                collection._handle_watchdog_event(event)

        try:
            observer = Observer()
            handler = Handler()
            for root in self._roots:
                observer.schedule(handler, str(root), recursive=True)
            observer.start()
            self._observer = observer
            return True
        except Exception as exc:
            self._observer_error = f"native filesystem observer unavailable: {exc}"
            self._observer = None
            return False

    def _handle_watchdog_event(self, event: Any) -> None:
        try:
            event_type = str(getattr(event, "event_type", ""))
            is_directory = bool(getattr(event, "is_directory", False))
            if event_type == "modified" and is_directory:
                return
            if event_type == "moved":
                source = getattr(event, "src_path", None)
                destination = getattr(event, "dest_path", None)
                paths = [source, destination]
            elif event_type in {"created", "modified", "deleted"}:
                paths = [getattr(event, "src_path", None)]
            else:
                return
            for value in paths:
                if value:
                    self._observe_path(Path(value))
        except Exception as exc:  # pragma: no cover - watchdog callbacks must keep running
            self._fatal_error = f"filesystem event handling failed: {exc}"

    def _observe_path(self, path: Path) -> None:
        root = self._root_for(path)
        if root is None or self._ignore.matches(path, root):
            return
        try:
            state = _state(path)
        except FileNotFoundError:
            self._observe_deleted(path, root)
            return
        except OSError:
            self._scan_errors += 1
            return

        event: Event | None = None
        with self._lock:
            previous = self._states.get(path)
            if previous is None:
                self._states[path] = state
                event = self._event(EventKind.FILESYSTEM_CREATED, path, state, root)
            elif previous != state:
                self._states[path] = state
                if not (previous.is_dir and state.is_dir):
                    event = self._event(EventKind.FILESYSTEM_MODIFIED, path, state, root)
        if event is not None:
            self._emit_event(event)

    def _observe_deleted(self, path: Path, root: Path) -> None:
        with self._lock:
            previous = self._states.pop(path, None)
        if previous is not None:
            self._emit_event(self._event(EventKind.FILESYSTEM_DELETED, path, previous, root))

    def _root_for(self, path: Path) -> Path | None:
        matches = [root for root in self._roots if path == root or path.is_relative_to(root)]
        return max(matches, key=lambda item: len(item.parts)) if matches else None

    def _event(self, kind: EventKind, path: Path, state: FileState, root: Path) -> Event:
        return Event.create(
            kind,
            source=self.name,
            occurred_at=self._clock(),
            payload={
                "path": str(path),
                "root": str(root),
                "is_dir": state.is_dir,
                "size": state.size,
                "mtime_ns": state.mtime_ns,
            },
        )

    def _emit_event(self, event: Event) -> None:
        if self._emit is None:
            return
        try:
            self._emit(event)
            self._events_emitted += 1
        except Exception as exc:  # pragma: no cover - defensive boundary around the queue
            self._fatal_error = f"event emission failed: {exc}"
            self._stop.set()

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._reconcile()
            except Exception as exc:  # pragma: no cover - defensive thread boundary
                self._fatal_error = f"filesystem polling failed: {exc}"
                return
            if self._stop.wait(self._poll_interval):
                return

    def _reconcile(self) -> None:
        if self._baseline_truncated:
            return
        merged: dict[Path, FileState] = {}
        truncated = False
        for watch_root in self._roots:
            scan = scan_tree(watch_root, self._ignore, self._max_files)
            merged.update(scan.states)
            truncated = truncated or scan.truncated
            self._accumulate_scan_errors(scan)
        if truncated:
            self._baseline_truncated = True
            return

        added: list[tuple[Path, FileState]] = []
        removed: list[tuple[Path, FileState]] = []
        changed: list[tuple[Path, FileState]] = []
        with self._lock:
            previous_states = self._states
            for path, state in merged.items():
                previous = previous_states.get(path)
                if previous is None:
                    added.append((path, state))
                elif previous != state and not (previous.is_dir and state.is_dir):
                    changed.append((path, state))
            for path, state in previous_states.items():
                if path not in merged:
                    removed.append((path, state))
            self._states = merged

        for path, state in sorted(added, key=lambda item: str(item[0])):
            root = self._root_for(path)
            if root is not None:
                self._emit_event(self._event(EventKind.FILESYSTEM_CREATED, path, state, root))
        for path, state in sorted(removed, key=lambda item: str(item[0])):
            root = self._root_for(path)
            if root is not None:
                self._emit_event(self._event(EventKind.FILESYSTEM_DELETED, path, state, root))
        for path, state in sorted(changed, key=lambda item: str(item[0])):
            root = self._root_for(path)
            if root is not None:
                self._emit_event(self._event(EventKind.FILESYSTEM_MODIFIED, path, state, root))

    def stop(self) -> CollectorStatus:
        self._stop.set()
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=10)
            except Exception as exc:  # pragma: no cover - observer shutdown boundary
                self._fatal_error = f"native observer shutdown failed: {exc}"
            self._observer = None
        if self._thread is not None:
            self._thread.join(timeout=30)
            self._thread = None

        if not self._baseline_truncated:
            try:
                self._reconcile()
            except Exception as exc:
                self._fatal_error = f"final filesystem reconciliation failed: {exc}"

        state = CollectorState.OK
        messages: list[str] = []
        if self._fatal_error:
            state = CollectorState.FAILED
            messages.append(self._fatal_error)
        elif self._baseline_truncated:
            state = CollectorState.DEGRADED
            messages.append(
                f"snapshot limit of {self._max_files} entries reached; changes are partial"
            )
        elif self._scan_errors:
            state = CollectorState.DEGRADED
            messages.append(
                f"{self._scan_errors} paths could not be inspected"
                + (f" (first: {self._scan_error_sample})" if self._scan_error_sample else "")
            )
        if self._observer_error:
            if state == CollectorState.OK:
                state = CollectorState.DEGRADED
            messages.append(self._observer_error)

        details: dict[str, Any] = {
            "backend": self._backend,
            "watch_roots": [str(root) for root in self._roots],
            "max_files": self._max_files,
            "baseline_truncated": self._baseline_truncated,
            "scan_errors": self._scan_errors,
            "events_emitted": self._events_emitted,
        }
        if self._observer_error:
            details["observer_error"] = self._observer_error

        return CollectorStatus(
            name=self.name,
            state=state,
            message="; ".join(messages) if messages else None,
            details=details,
            started_at=self._started_at,
            stopped_at=utc_now(),
        )
