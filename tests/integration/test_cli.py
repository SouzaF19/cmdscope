"""CLI end-to-end tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from cmdscope.cli import main

SCRIPT = """
from pathlib import Path

root = Path(__file__).parent
(root / "created.txt").write_text("hello\\n", encoding="utf-8")
"""


def _prepare(tmp_path: Path) -> tuple[Path, Path]:
    work = tmp_path / "work"
    work.mkdir()
    script = work / "demo.py"
    script.write_text(SCRIPT, encoding="utf-8")
    return work, script


def test_run_sessions_show_and_reports(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    _work, script = _prepare(tmp_path)
    database = tmp_path / "sessions.db"

    exit_code = main(
        [
            "--db",
            str(database),
            "--no-color",
            "run",
            "--",
            sys.executable,
            str(script),
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "CMDSCOPE - EXECUTION REPORT" in captured.out
    assert "created.txt" in captured.out

    exit_code = main(["--db", str(database), "sessions"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "S000001" in captured.out

    exit_code = main(["--db", str(database), "sessions", "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload[0]["ref"] == "S000001"

    exit_code = main(["--db", str(database), "show", "S000001"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "CMDSCOPE - EXECUTION REPORT" in captured.out

    exit_code = main(["--db", str(database), "report", "1", "--format", "json"])
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exit_code == 0
    assert report["schema"] == "cmdscope.report/v1"
    assert report["session"]["ref"] == "S000001"

    output = tmp_path / "report.html"
    exit_code = main(
        [
            "--db",
            str(database),
            "report",
            "1",
            "--format",
            "html",
            "--output",
            str(output),
        ]
    )
    capsys.readouterr()
    assert exit_code == 0
    assert "<!doctype html>" in output.read_text(encoding="utf-8")

    exit_code = main(
        [
            "--db",
            str(database),
            "report",
            "1",
            "--format",
            "html",
            "--output",
            str(output),
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "already exists" in captured.err

    exit_code = main(
        [
            "--db",
            str(database),
            "report",
            "1",
            "--format",
            "html",
            "--output",
            str(output),
            "--force",
        ]
    )
    capsys.readouterr()
    assert exit_code == 0


def test_run_returns_the_command_exit_code(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    database = tmp_path / "sessions.db"
    exit_code = main(
        [
            "--db",
            str(database),
            "--no-color",
            "run",
            "--no-report",
            "--",
            sys.executable,
            "-c",
            "import sys; sys.exit(5)",
        ]
    )
    capsys.readouterr()
    assert exit_code == 5


def test_environment_diff_is_redacted_in_the_report(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    database = tmp_path / "sessions.db"
    exit_code = main(
        [
            "--db",
            str(database),
            "--no-color",
            "run",
            "--include-environment",
            "--env",
            "CMDSCOPE_DEMO_TOKEN=super-secret",
            "--",
            sys.executable,
            "-c",
            "pass",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "CMDSCOPE_DEMO_TOKEN" in captured.out
    assert "super-secret" not in captured.out
    assert "[REDACTED]" in captured.out


def test_compare_two_sessions(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    _work, script = _prepare(tmp_path)
    database = tmp_path / "sessions.db"

    for _ in range(2):
        exit_code = main(
            [
                "--db",
                str(database),
                "--no-color",
                "run",
                "--no-report",
                "--",
                sys.executable,
                str(script),
            ]
        )
        capsys.readouterr()
        assert exit_code == 0

    exit_code = main(["--db", str(database), "compare", "1", "2", "--format", "json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["schema"] == "cmdscope.comparison/v1"
    assert payload["session_a"]["ref"] == "S000001"
    assert payload["session_b"]["ref"] == "S000002"

    exit_code = main(["--db", str(database), "compare", "1", "2"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "CMDSCOPE - SESSION COMPARISON" in captured.out


def test_doctor_and_config_commands(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    database = tmp_path / "sessions.db"

    exit_code = main(["--db", str(database), "doctor", "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["platform"]["name"]
    assert payload["database"]["opened"] is True

    exit_code = main(["config", "--init"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert (tmp_path / "cmdscope.toml").exists()

    exit_code = main(["config", "--init"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "already exists" in captured.err

    exit_code = main(["config", "--path"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "cmdscope.toml" in captured.out


def test_unknown_session_returns_an_error(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    database = tmp_path / "sessions.db"
    exit_code = main(["--db", str(database), "show", "42"])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "session not found" in captured.err


def test_run_without_a_command_is_a_usage_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as error:
        main(["run"])
    assert error.value.code == 2
