"""Session runner: coordinate collectors, child process, storage, and cleanup."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psutil

from cmdscope import __version__
from cmdscope.analysis.summary import summarize
from cmdscope.collectors.filesystem import FilesystemCollector
from cmdscope.collectors.network import NetworkCollector
from cmdscope.collectors.processes import ProcessCollector
from cmdscope.collectors.registry import RegistryCollector
from cmdscope.core.collector import Collector, CollectorContext
from cmdscope.core.events import (
    CollectorState,
    CollectorStatus,
    SessionStatus,
    utc_now,
)
from cmdscope.core.ignore import IgnoreRules
from cmdscope.core.registry import ProcessRegistry
from cmdscope.core.session import RunResult, SessionSummary
from cmdscope.errors import CommandLaunchError, StorageError
from cmdscope.platforms import current_platform_info, prepare_command
from cmdscope.privacy import environment_diff, format_command, sanitize_command
from cmdscope.storage.sqlite import Storage
from cmdscope.storage.writer import EventWriter, EventWriterStats


@dataclass(frozen=True, slots=True)
class RunOptions:
    """Fully resolved options for one monitored execution."""

    command: tuple[str, ...]
    cwd: Path
    watch_roots: tuple[Path, ...]
    ignore_patterns: tuple[str, ...]
    env_overrides: Mapping[str, str]
    database: Path
    enable_filesystem: bool = True
    enable_processes: bool = True
    enable_network: bool = True
    enable_registry: bool = True
    filesystem_poll_interval: float = 1.0
    process_poll_interval: float = 0.5
    network_poll_interval: float = 0.25
    max_files: int = 100_000
    max_events: int = 100_000
    max_sessions: int = 0
    use_native_events: bool = True
    capture_environment: bool = True


def execute(
    options: RunOptions,
    *,
    environment: Mapping[str, str] | None = None,
) -> RunResult:
    """Run one command and persist a complete session."""

    parent_environment = dict(os.environ if environment is None else environment)
    child_environment = dict(parent_environment)
    child_environment.update(options.env_overrides)
    env_diff = (
        environment_diff(parent_environment, child_environment)
        if options.capture_environment
        else {}
    )

    sanitized = sanitize_command(options.command)
    display = format_command(sanitized)
    platform_info = current_platform_info()
    database = options.database.expanduser().resolve()
    storage = Storage(database)
    database_files = (
        database,
        Path(f"{database}-wal"),
        Path(f"{database}-shm"),
        Path(f"{database}-journal"),
    )
    ignore = IgnoreRules(options.ignore_patterns, exclude_paths=database_files)

    session_id: int | None = None
    writer: EventWriter | None = None
    statuses: list[CollectorStatus] = []
    active: list[Collector] = []
    process: subprocess.Popen[bytes] | None = None
    finalized = False

    def add_disabled(name: str) -> None:
        if name not in {status.name for status in statuses}:
            statuses.append(
                CollectorStatus(
                    name=name,
                    state=CollectorState.DISABLED,
                    message="disabled by configuration",
                )
            )

    try:
        session_id = storage.create_session(
            command=sanitized,
            command_display=display,
            cwd=str(options.cwd),
            platform_name=platform_info.name,
            created_at=utc_now(),
            env_diff=env_diff,
            options={
                "cmdscope_version": __version__,
                "watch_roots": [str(root) for root in options.watch_roots],
                "ignore": list(options.ignore_patterns),
                "filesystem": {
                    "enabled": options.enable_filesystem,
                    "native_events": options.use_native_events,
                    "poll_interval": options.filesystem_poll_interval,
                    "max_files": options.max_files,
                },
                "processes": {
                    "enabled": options.enable_processes,
                    "poll_interval": options.process_poll_interval,
                },
                "network": {
                    "enabled": options.enable_network,
                    "poll_interval": options.network_poll_interval,
                },
                "storage": {"max_events": options.max_events},
            },
        )

        writer = EventWriter(database, session_id, options.max_events)
        writer.start()
        registry = ProcessRegistry()

        def context(root_pid: int) -> CollectorContext:
            assert writer is not None
            return CollectorContext(
                session_id=session_id or 0,
                watch_roots=options.watch_roots,
                ignore=ignore,
                root_pid=root_pid,
                command_name=Path(options.command[0]).name,
                registry=registry,
                emit=writer.emit,
                filesystem_poll_interval=options.filesystem_poll_interval,
                process_poll_interval=options.process_poll_interval,
                network_poll_interval=options.network_poll_interval,
                max_files=options.max_files,
                use_native_events=options.use_native_events,
            )

        if options.enable_filesystem:
            filesystem = FilesystemCollector(
                max_files=options.max_files,
                poll_interval=options.filesystem_poll_interval,
                use_native_events=options.use_native_events,
            )
            try:
                filesystem.start(context(0))
                active.append(filesystem)
            except Exception as exc:
                statuses.append(
                    CollectorStatus(
                        name=filesystem.name,
                        state=CollectorState.FAILED,
                        message=f"collector failed to start: {exc}",
                    )
                )
        else:
            add_disabled("filesystem")

        if not options.enable_processes:
            add_disabled("processes")
        if not options.enable_network:
            add_disabled("network")

        storage.mark_command_started(session_id, utc_now())
        prepared = prepare_command(options.command, child_environment)
        spawn_started = time.monotonic()
        try:
            process = _spawn(prepared, options.cwd, child_environment)
        except OSError as exc:
            error = f"could not start command {options.command[0]!r}: {exc}"
            exit_code = 127 if isinstance(exc, FileNotFoundError) else 126
            _finalize(
                storage,
                session_id,
                status=SessionStatus.FAILED,
                ended_at=utc_now(),
                duration_s=0.0,
                exit_code=exit_code,
                interrupted=False,
                error=error,
                statuses=statuses,
                active=active,
                writer=writer,
                event_limit=options.max_events,
            )
            finalized = True
            raise CommandLaunchError(error, exit_code=exit_code, session_id=session_id) from exc

        root_pid = process.pid
        if options.enable_processes:
            process_collector = ProcessCollector(poll_interval=options.process_poll_interval)
            process_collector.set_registry(registry)
            try:
                process_collector.start(context(root_pid))
                active.append(process_collector)
            except Exception as exc:
                statuses.append(
                    CollectorStatus(
                        name=process_collector.name,
                        state=CollectorState.FAILED,
                        message=f"collector failed to start: {exc}",
                    )
                )
                registry.add(root_pid, Path(options.command[0]).name)
        if options.enable_registry:
            registry_collector = RegistryCollector(
                watch_roots=context.watch_roots,
                poll_interval=options.filesystem_poll_interval,
            )
            try:
                registry_collector.start(context)
                active.append(registry_collector)
            except Exception as exc:
                statuses.append(
                    CollectorStatus(
                        name=registry_collector.name,
                        state=CollectorState.FAILED,
                        message=f"collector failed to start: {exc}",
                    )
                )
        else:
            registry.add(root_pid, Path(options.command[0]).name)

        if options.enable_network:
            network_collector = NetworkCollector(poll_interval=options.network_poll_interval)
            try:
                network_collector.start(context(root_pid))
                active.append(network_collector)
            except Exception as exc:
                statuses.append(
                    CollectorStatus(
                        name=network_collector.name,
                        state=CollectorState.FAILED,
                        message=f"collector failed to start: {exc}",
                    )
                )

        exit_code, interrupted = _wait_for_process(process)
        ended_at = utc_now()
        duration = max(0.0, time.monotonic() - spawn_started)

        for collector in reversed(active):
            statuses.append(_stop_collector(collector))

        writer_stats, writer_error = _close_writer(writer)
        if writer_stats is not None and writer_stats.truncated:
            statuses.append(
                CollectorStatus(
                    name="event-writer",
                    state=CollectorState.DEGRADED,
                    message=(
                        f"event limit of {options.max_events} reached; "
                        f"{writer_stats.dropped} events were not stored"
                    ),
                    details={
                        "written": writer_stats.written,
                        "dropped": writer_stats.dropped,
                    },
                )
            )
        if writer_error:
            statuses.append(
                CollectorStatus(
                    name="event-writer",
                    state=CollectorState.FAILED,
                    message=writer_error,
                )
            )

        if writer_error:
            session_status = SessionStatus.FAILED
            session_error = writer_error
            effective_exit = 1
        elif interrupted:
            session_status = SessionStatus.INTERRUPTED
            session_error = None
            effective_exit = exit_code
        else:
            session_status = SessionStatus.COMPLETED
            session_error = None
            effective_exit = exit_code

        storage.finish_session(
            session_id,
            status=session_status,
            ended_at=ended_at,
            duration_s=duration,
            exit_code=exit_code,
            interrupted=interrupted,
            error=session_error,
        )
        storage.set_collector_statuses(session_id, statuses)
        finalized = True

        try:
            storage.prune(options.max_sessions)
        except StorageError as exc:
            statuses.append(
                CollectorStatus(
                    name="storage",
                    state=CollectorState.DEGRADED,
                    message=f"session pruning failed: {exc}",
                )
            )
            storage.set_collector_statuses(session_id, statuses)

        session = storage.require_session(str(session_id))
        events = storage.get_events(session_id)
        summary = summarize(session, events)
        return RunResult(
            session_id=session_id,
            exit_code=effective_exit,
            interrupted=interrupted,
            event_count=len(events),
            events_truncated=bool(writer_stats and writer_stats.truncated),
            summary=summary,
        )
    except CommandLaunchError:
        raise
    except Exception as exc:
        if session_id is not None and not finalized:
            try:
                for collector in reversed(active):
                    statuses.append(_stop_collector(collector))
                _finalize(
                    storage,
                    session_id,
                    status=SessionStatus.FAILED,
                    ended_at=utc_now(),
                    duration_s=0.0,
                    exit_code=1,
                    interrupted=False,
                    error=f"internal error: {exc}",
                    statuses=statuses,
                    active=[],
                    writer=writer,
                    event_limit=options.max_events,
                )
            except Exception:
                pass
        if process is not None and process.poll() is None:
            _terminate_tree(process)
        raise
    finally:
        if process is not None and process.poll() is None:
            _terminate_tree(process)
        storage.close()


def _spawn(
    prepared: list[str],
    cwd: Path,
    environment: Mapping[str, str],
) -> subprocess.Popen[bytes]:
    if os.name == "nt":
        creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return subprocess.Popen(
            prepared,
            cwd=str(cwd),
            env=dict(environment),
            creationflags=creation_flags,
        )
    return subprocess.Popen(
        prepared,
        cwd=str(cwd),
        env=dict(environment),
        start_new_session=True,
    )


def _wait_for_process(process: subprocess.Popen[bytes]) -> tuple[int, bool]:
    """Wait for the child, forwarding an interactive interruption to its tree."""

    previous_handlers: dict[int, object] = {}

    def interrupt(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    for signum in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if signum is None:
            continue
        try:
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, interrupt)
        except (OSError, ValueError):
            continue

    try:
        return process.wait(), False
    except KeyboardInterrupt:
        _terminate_tree(process)
        try:
            return process.wait(timeout=10), True
        except subprocess.TimeoutExpired:
            process.kill()
            return process.wait(), True
    finally:
        for signum, handler in previous_handlers.items():
            with contextlib.suppress(OSError, ValueError):
                signal.signal(signum, handler)  # type: ignore[arg-type]


def _terminate_tree(process: subprocess.Popen[bytes]) -> None:
    children: list[psutil.Process] = []
    try:
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
    except psutil.Error:
        children = []

    for child in children:
        with contextlib.suppress(psutil.Error):
            child.terminate()
    with contextlib.suppress(OSError):
        process.terminate()

    _gone, alive = psutil.wait_procs(children, timeout=3)
    for child in alive:
        with contextlib.suppress(psutil.Error):
            child.kill()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=3)


def _stop_collector(collector: Collector) -> CollectorStatus:
    try:
        return collector.stop()
    except Exception as exc:
        return CollectorStatus(
            name=collector.name,
            state=CollectorState.FAILED,
            message=f"collector shutdown failed: {exc}",
            stopped_at=utc_now(),
        )


def _close_writer(writer: EventWriter) -> tuple[EventWriterStats | None, str | None]:
    try:
        return writer.close(), None
    except StorageError as exc:
        return None, str(exc)


def _finalize(
    storage: Storage,
    session_id: int,
    *,
    status: SessionStatus,
    ended_at: datetime,
    duration_s: float,
    exit_code: int,
    interrupted: bool,
    error: str,
    statuses: list[CollectorStatus],
    active: list[Collector],
    writer: EventWriter | None,
    event_limit: int,
) -> None:
    """Finalize a session after a launch or internal failure."""

    for collector in reversed(active):
        statuses.append(_stop_collector(collector))
    if writer is not None:
        stats, writer_error = _close_writer(writer)
    else:
        stats, writer_error = None, None
    if writer_error:
        statuses.append(
            CollectorStatus(
                name="event-writer",
                state=CollectorState.FAILED,
                message=writer_error,
            )
        )
    if stats is not None and stats.truncated:
        statuses.append(
            CollectorStatus(
                name="event-writer",
                state=CollectorState.DEGRADED,
                message=(
                    f"event limit of {event_limit} reached; {stats.dropped} events were not stored"
                ),
            )
        )
    for name in ("processes", "network"):
        if name not in {collector.name for collector in statuses}:
            statuses.append(
                CollectorStatus(
                    name=name,
                    state=CollectorState.FAILED,
                    message="not started: command could not be launched",
                )
            )
    storage.finish_session(
        session_id,
        status=status,
        ended_at=ended_at,
        duration_s=duration_s,
        exit_code=exit_code,
        interrupted=interrupted,
        error=error,
    )
    storage.set_collector_statuses(session_id, statuses)


__all__ = ["RunOptions", "SessionSummary", "execute"]
