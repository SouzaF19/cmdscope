"""Platform capability and path tests."""

from __future__ import annotations

import os
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from cmdscope import __version__
from cmdscope.core.ignore import DEFAULT_IGNORE_PATTERNS, IgnoreRules
from cmdscope.platforms import (
    config_dir,
    current_platform_info,
    data_dir,
    is_elevated,
    prepare_command,
)
from cmdscope.platforms.base import fallback_info


def test_data_and_config_dirs_follow_platform_rules() -> None:
    env = {
        "USERPROFILE": "C:/Users/tester",
        "LOCALAPPDATA": "C:/Users/tester/AppData/Local",
        "APPDATA": "C:/Users/tester/AppData/Roaming",
        "HOME": "/home/tester",
        "XDG_DATA_HOME": "/xdg/data",
        "XDG_CONFIG_HOME": "/xdg/config",
    }
    assert data_dir("windows", env) == Path("C:/Users/tester/AppData/Local/cmdscope")
    assert config_dir("windows", env) == Path("C:/Users/tester/AppData/Roaming/cmdscope")
    assert data_dir("linux", env) == Path("/xdg/data/cmdscope")
    assert config_dir("linux", env) == Path("/xdg/config/cmdscope")
    assert data_dir("macos", env) == Path("/home/tester/Library/Application Support/cmdscope")


def test_current_platform_info_describes_the_host() -> None:
    info = current_platform_info()
    assert info.name in {"windows", "linux", "macos", "unknown"}
    assert info.notes
    assert info.default_database_path.name == "cmdscope.db"


def test_fallback_platform_is_conservative() -> None:
    info = fallback_info({"HOME": "/home/tester"})
    assert info.native_filesystem_events is False
    assert info.fs_backend == "polling"


def test_prepare_command_rejects_empty_command() -> None:
    try:
        prepare_command([])
    except ValueError:
        return
    raise AssertionError("empty command should raise")


def test_prepare_command_launches_windows_batch_shims(tmp_path: Path) -> None:
    script = tmp_path / "hello.cmd"
    script.write_text("@echo off\r\necho batch-ok\r\n", encoding="utf-8")
    prepared = prepare_command([str(script)])
    if os.name == "nt":
        assert prepared[0].lower().endswith(("cmd.exe", "comspec"))
        completed = subprocess.run(
            prepared, capture_output=True, text=True, check=False, timeout=30
        )
        assert "batch-ok" in completed.stdout
    else:
        assert prepared == [str(script)]


def test_is_elevated_returns_boolean() -> None:
    assert isinstance(is_elevated(), bool)


def test_ignore_rules_match_components_and_globs(tmp_path: Path) -> None:
    root = tmp_path
    rules = IgnoreRules(DEFAULT_IGNORE_PATTERNS)
    assert rules.matches(root / ".git" / "config", root)
    assert rules.matches(root / "module.pyc", root)
    assert not rules.matches(root / "src" / "main.py", root)
    assert not rules.matches(root, root)


def test_ignore_rules_support_explicit_exclusions(tmp_path: Path) -> None:
    root = tmp_path
    database = root / "cmdscope.db"
    rules = IgnoreRules((), exclude_paths=(database,))
    assert rules.matches(database, root)
    assert not rules.matches(root / "other.db", root)


def test_version_matches_distribution_metadata() -> None:
    try:
        installed = version("cmdscope")
    except PackageNotFoundError:  # pragma: no cover - source checkout
        return
    assert __version__ == installed
