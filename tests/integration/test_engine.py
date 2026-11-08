"""End-to-end engine tests with real child processes."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cmdscope.core.engine import RunOptions, execute
from cmdscope.core.events import CollectorState, EventKind, SessionStatus
from cmdscope.errors import CommandLaunchError
from cmdscope.storage.sqlite import Storage

SCRIPT = """
from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

root = Path(__file__).parent
(root / "created.txt").write_text("new\\n", encoding="utf-8")
(root / "modified.txt").write_text("changed\\n", encoding="utf-8")
(root / "deleted.txt").unlink(missing_ok=True)
(root / "folder").mkdir(exist_ok=True)
(root / "folder" / "nested.txt").write_text("nested\\n", encoding="utf-8")

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.4)"])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    time.sleep(1.2)
child.wait(timeout=10)
"""


def _options(work: Path, database: Path, **overrides: object) -> RunOptions:
    values: dict[str, object] = {
        "command": (sys.executable, str(work / "demo.py")),
        "cwd": work,
        "watch_roots": (work,),
        "ignore_patterns": (),
        "env_overrides": {},
        "database": database,
        "filesystem_poll_interval": 0.02,
        "process_poll_interval": 0.05,
        "network_poll_interval": 0.05,
        "max_files": 10_000,
        "max_events": 10_000,
        "max_sessions": 0,
        "use_native_events": False,
    }
    values.update(overrides)
    return RunOptions(**values)  # type: ignore[arg-type]


def _prepare_work(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    (work / "demo.py").write_text(SCRIPT, encoding="utf-8")
    (work / "modified.txt").write_text("original\n", encoding="utf-8")
    (work / "deleted.txt").write_text("remove me\n", encoding="utf-8")
    return work


def test_session_records_files_processes_and_network(tmp_path: Path) -> None:
    work = _prepare_work(tmp_path)
    database = tmp_path / "data" / "sessions.db"
    result = execute(
        _options(
            work,
            database,
            env_overrides={
                "CMDSCOPE_TEST_VALUE": "visible",
                "CMDSCOPE_TEST_TOKEN": "super-secret",
            },
        )
    )

    assert result.exit_code == 0
    assert result.interrupted is False
    session = result.summary.session
    assert session.status == SessionStatus.COMPLETED
    assert session.exit_code == 0
    assert session.duration_s is not None and session.duration_s > 0

    with Storage(database) as storage:
        events = storage.get_events(session.id)
        kinds = {event.kind for event in events}

    assert EventKind.FILESYSTEM_CREATED in kinds
    assert EventKind.FILESYSTEM_MODIFIED in kinds
    assert EventKind.FILESYSTEM_DELETED in kinds
    assert EventKind.PROCESS_STARTED in kinds
    assert EventKind.NETWORK_CONNECTION in kinds

    added = session.env_diff.get("added")
    assert isinstance(added, dict)
    assert added["CMDSCOPE_TEST_VALUE"] == "visible"
    assert added["CMDSCOPE_TEST_TOKEN"] == "[REDACTED]"

    assert "super-secret" not in database.read_bytes().decode("utf-8", errors="ignore")


def test_nonzero_exit_code_is_preserved(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    database = tmp_path / "sessions.db"
    result = execute(
        _options(
            work,
            database,
            command=(sys.executable, "-c", "import sys; sys.exit(7)"),
        )
    )
    assert result.exit_code == 7
    assert result.summary.session.status == SessionStatus.COMPLETED
    assert result.summary.session.exit_code == 7


def test_missing_command_is_reported_and_recorded(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    database = tmp_path / "sessions.db"

    with pytest.raises(CommandLaunchError) as error:
        execute(
            _options(
                work,
                database,
                command=("cmdscope-command-that-does-not-exist",),
            )
        )
    assert error.value.exit_code == 127
    assert error.value.session_id is not None

    with Storage(database) as storage:
        session = storage.require_session(str(error.value.session_id))
        assert session.status == SessionStatus.FAILED
        assert session.error is not None
        states = {collector.name: collector.state for collector in session.collectors}
        assert states["processes"] == CollectorState.FAILED


def test_event_limit_truncates_and_is_reported(tmp_path: Path) -> None:
    work = _prepare_work(tmp_path)
    database = tmp_path / "sessions.db"
    result = execute(_options(work, database, max_events=1))

    assert result.events_truncated is True
    with Storage(database) as storage:
        assert storage.count_events(result.session_id) == 1
        session = storage.require_session(str(result.session_id))
        states = {collector.name: collector for collector in session.collectors}
        assert states["event-writer"].state == CollectorState.DEGRADED


def test_database_inside_the_watch_root_is_not_reported(tmp_path: Path) -> None:
    work = _prepare_work(tmp_path)
    database = work / "sessions.db"
    result = execute(_options(work, database))

    with Storage(database) as storage:
        events = storage.get_events(result.session_id)
    paths = {str(event.payload.get("path", "")) for event in events}
    assert not any("sessions.db" in path for path in paths)


def test_disabled_collectors_are_recorded(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    database = tmp_path / "sessions.db"
    result = execute(
        _options(
            work,
            database,
            command=(sys.executable, "-c", "pass"),
            enable_filesystem=False,
            enable_network=False,
        )
    )
    states = {collector.name: collector.state for collector in result.summary.session.collectors}
    assert states["filesystem"] == CollectorState.DISABLED
    assert states["network"] == CollectorState.DISABLED
    assert states["processes"] == CollectorState.OK


def test_two_sessions_can_be_compared(tmp_path: Path) -> None:
    from cmdscope.analysis.comparison import compare_sessions

    work = _prepare_work(tmp_path)
    database = tmp_path / "sessions.db"
    first = execute(_options(work, database))
    second = execute(
        _options(
            work,
            database,
            command=(
                sys.executable,
                "-c",
                "import runpy; from pathlib import Path; "
                "runpy.run_path('demo.py', run_name='__main__'); "
                "Path('only-in-second.txt').write_text('x')",
            ),
        )
    )

    with Storage(database) as storage:
        result = compare_sessions(storage, str(first.session_id), str(second.session_id))
    labels = {item.label for item in result.only_in_b}
    assert any(label.endswith("only-in-second.txt") for label in labels)
    assert result.common_count > 0
