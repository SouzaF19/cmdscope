"""Process collector tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from cmdscope.collectors.processes import ProcessCollector
from cmdscope.core.events import EventKind
from cmdscope.core.registry import ProcessRegistry
from tests.support import make_context


def test_process_collector_observes_start_and_finish(tmp_path: Path) -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.5)"])
    events: list = []
    registry = ProcessRegistry()
    collector = ProcessCollector(poll_interval=0.05)
    collector.set_registry(registry)
    collector.start(make_context(tmp_path, events.append, registry=registry, root_pid=child.pid))

    started = [event for event in events if event.kind == EventKind.PROCESS_STARTED]
    assert any(event.payload["pid"] == child.pid for event in started)

    child.wait(timeout=30)
    status = collector.stop()

    finished = [event for event in events if event.kind == EventKind.PROCESS_FINISHED]
    assert any(event.payload["pid"] == child.pid for event in finished)
    assert status.details["root_pid"] == child.pid


def test_process_collector_handles_a_root_that_already_exited(tmp_path: Path) -> None:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=30)

    events: list = []
    registry = ProcessRegistry()
    collector = ProcessCollector(poll_interval=0.05)
    collector.set_registry(registry)
    collector.start(make_context(tmp_path, events.append, registry=registry, root_pid=child.pid))
    status = collector.stop()

    started = [event for event in events if event.kind == EventKind.PROCESS_STARTED]
    finished = [event for event in events if event.kind == EventKind.PROCESS_FINISHED]
    assert any(event.payload["pid"] == child.pid for event in started)
    assert any(event.payload.get("reason") == "exited_before_observation" for event in finished)
    assert status is not None


def test_process_collector_registers_pids_for_other_collectors(tmp_path: Path) -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.5)"])
    events: list = []
    registry = ProcessRegistry()
    collector = ProcessCollector(poll_interval=0.05)
    collector.set_registry(registry)
    collector.start(make_context(tmp_path, events.append, registry=registry, root_pid=child.pid))
    assert child.pid in registry.snapshot()
    child.wait(timeout=30)
    collector.stop()
    assert child.pid not in registry.snapshot()
