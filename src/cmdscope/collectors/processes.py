"""Process-tree collector based on bounded psutil polling."""

from __future__ import annotations

import shlex
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import psutil

from cmdscope.core.collector import CollectorContext
from cmdscope.core.events import (
    CollectorState,
    CollectorStatus,
    Event,
    EventKind,
    utc_now,
)
from cmdscope.core.registry import ProcessRegistry
from cmdscope.privacy import sanitize_command


@dataclass(slots=True)
class _ProcessInfo:
    pid: int
    ppid: int | None
    name: str
    exe: str | None
    cmdline: tuple[str, ...]
    create_time: float | None
    first_seen: datetime


class ProcessCollector:
    """Track the root process and its descendants.

    Discovery is poll-based. A child that starts and exits between two polls can
    be missed; that limitation is part of the collector status. PIDs are matched
    with their creation time to avoid attributing a reused PID to the session.
    """

    name = "processes"

    def __init__(self, *, poll_interval: float) -> None:
        self._poll_interval = poll_interval
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._known: dict[int, _ProcessInfo] = {}
        self._registry = ProcessRegistry()
        self._emit: Callable[[Event], None] | None = None
        self._root_pid = 0
        self._command_name = ""
        self._thread: threading.Thread | None = None
        self._access_denied = False
        self._error: str | None = None
        self._root_vanished = False
        self._started_at: datetime | None = None
        self._events_emitted = 0

    def set_registry(self, registry: ProcessRegistry) -> None:
        """Use the session-wide registry shared with the network collector."""

        self._registry = registry

    def start(self, context: CollectorContext) -> None:
        self._root_pid = context.root_pid
        self._command_name = context.command_name
        self._registry = context.registry
        self._emit = context.emit
        self._started_at = utc_now()

        try:
            process = psutil.Process(self._root_pid)
            self._register(process)
        except psutil.NoSuchProcess:
            self._root_vanished = True
            self._emit_started(
                pid=self._root_pid,
                ppid=None,
                name=self._command_name,
                exe=None,
                cmdline=(),
                create_time=None,
            )
            self._emit_finished(
                pid=self._root_pid,
                name=self._command_name,
                exe=None,
                cmdline=(),
                reason="exited_before_observation",
                first_seen=self._started_at,
            )
        except psutil.AccessDenied as exc:
            self._access_denied = True
            self._error = f"cannot inspect root process: {exc}"

        self._thread = threading.Thread(
            target=self._poll_loop, name="cmdscope-process-poller", daemon=True
        )
        self._thread.start()

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll()
            except Exception as exc:  # pragma: no cover - defensive thread boundary
                self._error = f"process polling failed: {exc}"
                return
            if self._stop.wait(self._poll_interval):
                return

    def _poll(self) -> None:
        self._discover_descendants()
        self._reap_gone()

    def _discover_descendants(self) -> None:
        with self._lock:
            known_pids = list(self._known)
        for pid in known_pids:
            try:
                process = psutil.Process(pid)
                if not self._still_same_process(process, pid):
                    continue
                children = process.children(recursive=False)
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                self._access_denied = True
                continue
            for child in children:
                self._register(child)

        try:
            root = psutil.Process(self._root_pid)
            for child in root.children(recursive=True):
                self._register(child)
        except psutil.NoSuchProcess:
            return
        except psutil.AccessDenied:
            self._access_denied = True

    def _register(self, process: psutil.Process) -> None:
        try:
            pid = process.pid
        except psutil.NoSuchProcess:
            return
        with self._lock:
            if pid in self._known:
                return

        ppid: int | None = None
        name = f"pid-{pid}"
        exe: str | None = None
        cmdline_value: list[str] = []
        create_time: float | None = None
        try:
            ppid = process.ppid()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            ppid = None
        try:
            name = process.name() or name
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            name = f"pid-{pid}"
        try:
            exe = process.exe()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            exe = None
        try:
            cmdline_value = process.cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            cmdline_value = []
        try:
            create_time = process.create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            create_time = None

        cmdline = sanitize_command(cmdline_value)
        info = _ProcessInfo(
            pid=pid,
            ppid=ppid,
            name=name,
            exe=exe,
            cmdline=cmdline,
            create_time=create_time,
            first_seen=utc_now(),
        )
        with self._lock:
            if pid in self._known:
                return
            self._known[pid] = info
        self._registry.add(pid, name)
        self._emit_started(
            pid=pid,
            ppid=ppid,
            name=name,
            exe=exe,
            cmdline=cmdline,
            create_time=create_time,
        )

    def _still_same_process(self, process: psutil.Process, pid: int) -> bool:
        with self._lock:
            info = self._known.get(pid)
        if info is None:
            return False
        try:
            return abs(process.create_time() - (info.create_time or 0.0)) < 0.01
        except psutil.NoSuchProcess:
            return False
        except psutil.AccessDenied:
            self._access_denied = True
            return True

    def _reap_gone(self) -> None:
        with self._lock:
            pids = list(self._known)
        for pid in pids:
            with self._lock:
                info = self._known.get(pid)
            if info is None:
                continue
            try:
                process = psutil.Process(pid)
                if process.is_running() and self._still_same_process(process, pid):
                    continue
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                self._access_denied = True
                continue
            self._mark_finished(pid, info, reason="no_longer_observed")

    def _mark_finished(self, pid: int, info: _ProcessInfo, *, reason: str) -> None:
        with self._lock:
            current = self._known.pop(pid, None)
        if current is None:
            return
        self._registry.remove(pid)
        self._emit_finished(
            pid=pid,
            name=info.name,
            exe=info.exe,
            cmdline=info.cmdline,
            reason=reason,
            first_seen=info.first_seen,
        )

    def _emit_started(
        self,
        *,
        pid: int,
        ppid: int | None,
        name: str,
        exe: str | None,
        cmdline: tuple[str, ...],
        create_time: float | None,
    ) -> None:
        self._emit_event(
            Event.create(
                EventKind.PROCESS_STARTED,
                source=self.name,
                payload={
                    "pid": pid,
                    "ppid": ppid,
                    "name": name,
                    "exe": exe,
                    "cmdline": list(cmdline),
                    "command": shlex.join(cmdline),
                    "create_time": create_time,
                },
            )
        )

    def _emit_finished(
        self,
        *,
        pid: int,
        name: str,
        exe: str | None,
        cmdline: tuple[str, ...],
        reason: str,
        first_seen: datetime,
    ) -> None:
        now = utc_now()
        self._emit_event(
            Event.create(
                EventKind.PROCESS_FINISHED,
                source=self.name,
                occurred_at=now,
                payload={
                    "pid": pid,
                    "name": name,
                    "exe": exe,
                    "cmdline": list(cmdline),
                    "command": shlex.join(cmdline),
                    "reason": reason,
                    "observed_lifetime_s": max(0.0, (now - first_seen).total_seconds()),
                },
            )
        )

    def _emit_event(self, event: Event) -> None:
        if self._emit is None:
            return
        try:
            self._emit(event)
            self._events_emitted += 1
        except Exception as exc:  # pragma: no cover - defensive queue boundary
            self._error = f"event emission failed: {exc}"
            self._stop.set()

    def stop(self) -> CollectorStatus:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=30)
            self._thread = None
        try:
            self._poll()
        except Exception as exc:
            self._error = f"final process poll failed: {exc}"

        with self._lock:
            survivors = list(self._known.values())
        for info in survivors:
            self._emit_event(
                Event.create(
                    EventKind.PROCESS_STILL_RUNNING,
                    source=self.name,
                    payload={
                        "pid": info.pid,
                        "ppid": info.ppid,
                        "name": info.name,
                        "exe": info.exe,
                        "cmdline": list(info.cmdline),
                        "command": shlex.join(info.cmdline),
                    },
                )
            )

        state = CollectorState.OK
        messages: list[str] = []
        if self._error:
            state = CollectorState.FAILED
            messages.append(self._error)
        elif self._access_denied:
            state = CollectorState.DEGRADED
            messages.append("some processes could not be inspected (access denied)")
        if self._root_vanished:
            messages.append("root process exited before process observation began")

        return CollectorStatus(
            name=self.name,
            state=state,
            message="; ".join(messages) if messages else None,
            details={
                "root_pid": self._root_pid,
                "observed_processes": self._events_emitted,
                "still_running": len(survivors),
                "access_denied": self._access_denied,
            },
            started_at=self._started_at,
            stopped_at=utc_now(),
        )
