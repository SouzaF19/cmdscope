"""Filesystem collector tests."""

from __future__ import annotations

from pathlib import Path

from cmdscope.collectors.filesystem import FilesystemCollector, scan_tree
from cmdscope.core.events import CollectorState, EventKind
from cmdscope.core.ignore import IgnoreRules
from tests.support import make_context


def test_scan_tree_reports_metadata_and_respects_ignores(tmp_path: Path) -> None:
    (tmp_path / "keep.txt").write_text("hello", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "file.bin").write_bytes(b"1234")

    result = scan_tree(tmp_path, IgnoreRules(), max_files=100)
    assert result.truncated is False
    paths = {str(path) for path in result.states}
    assert str(tmp_path / "keep.txt") in paths
    assert str(tmp_path / "nested" / "file.bin") in paths
    assert str(tmp_path / ".git" / "config") not in paths
    state = result.states[tmp_path / "keep.txt"]
    assert state.size == 5
    assert state.is_dir is False


def test_scan_tree_marks_truncation(tmp_path: Path) -> None:
    for index in range(10):
        (tmp_path / f"file-{index}.txt").write_text("x", encoding="utf-8")
    result = scan_tree(tmp_path, IgnoreRules(), max_files=3)
    assert result.truncated is True
    assert len(result.states) == 3


def test_collector_reports_created_modified_and_deleted(tmp_path: Path) -> None:
    (tmp_path / "modified.txt").write_text("before", encoding="utf-8")
    (tmp_path / "deleted.txt").write_text("bye", encoding="utf-8")
    events: list = []

    collector = FilesystemCollector(max_files=1000, poll_interval=0.02, use_native_events=False)
    collector.start(make_context(tmp_path, events.append))

    (tmp_path / "created.txt").write_text("new", encoding="utf-8")
    (tmp_path / "modified.txt").write_text("after", encoding="utf-8")
    (tmp_path / "deleted.txt").unlink()
    (tmp_path / "folder").mkdir()
    (tmp_path / "folder" / "nested.txt").write_text("nested", encoding="utf-8")

    status = collector.stop()
    assert status.state == CollectorState.OK

    by_kind: dict[str, set[str]] = {}
    for event in events:
        by_kind.setdefault(str(event.kind), set()).add(str(event.payload["path"]))

    created = by_kind[EventKind.FILESYSTEM_CREATED]
    assert str(tmp_path / "created.txt") in created
    assert str(tmp_path / "folder") in created
    assert str(tmp_path / "folder" / "nested.txt") in created

    modified = by_kind[EventKind.FILESYSTEM_MODIFIED]
    assert str(tmp_path / "modified.txt") in modified

    deleted = by_kind[EventKind.FILESYSTEM_DELETED]
    assert str(tmp_path / "deleted.txt") in deleted


def test_collector_ignores_configured_paths(tmp_path: Path) -> None:
    events: list = []
    database = tmp_path / "cmdscope.db"
    collector = FilesystemCollector(max_files=1000, poll_interval=0.02, use_native_events=False)
    context = make_context(
        tmp_path,
        events.append,
        ignore=IgnoreRules((), exclude_paths=(database,)),
    )
    collector.start(context)
    database.write_text("data", encoding="utf-8")
    (tmp_path / "other.txt").write_text("data", encoding="utf-8")
    collector.stop()

    paths = {str(event.payload["path"]) for event in events}
    assert str(database) not in paths
    assert str(tmp_path / "other.txt") in paths


def test_collector_is_degraded_when_the_snapshot_limit_is_hit(tmp_path: Path) -> None:
    for index in range(6):
        (tmp_path / f"file-{index}.txt").write_text("x", encoding="utf-8")
    events: list = []
    collector = FilesystemCollector(max_files=2, poll_interval=0.02, use_native_events=False)
    collector.start(make_context(tmp_path, events.append))
    status = collector.stop()
    assert status.state == CollectorState.DEGRADED
    assert status.details["baseline_truncated"] is True
    assert status.message is not None
