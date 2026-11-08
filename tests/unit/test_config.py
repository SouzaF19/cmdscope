"""Configuration loading and validation tests."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from cmdscope.config import (
    Config,
    config_as_dict,
    default_config_text,
    find_config_path,
    load_config,
    resolve_watch_roots,
)
from cmdscope.errors import ConfigurationError


def test_defaults_are_used_when_no_file_exists(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path, environment={})
    assert config.source_path is None
    assert config.filesystem.enabled is True
    assert config.network.poll_interval == pytest.approx(0.25)
    assert config.processes.poll_interval == pytest.approx(0.5)
    assert config.storage.max_sessions == 0
    assert config.warnings == ()


def test_explicit_configuration_is_loaded(tmp_path: Path) -> None:
    path = tmp_path / "cmdscope.toml"
    path.write_text(
        """
[filesystem]
watch = ["work"]
ignore = ["*.tmp"]
max_files = 42
native_events = false

[processes]
enabled = false

[network]
poll_interval = 1.5

[privacy]
capture_environment = false
show_environment = true

[storage]
database = "data/sessions.db"
max_sessions = 5
max_events = 99

[report]
max_events = 7
format = "json"
""",
        encoding="utf-8",
    )
    config = load_config(path, cwd=tmp_path, environment={})
    assert config.source_path == path.resolve()
    assert config.filesystem.watch == ("work",)
    assert config.filesystem.ignore == ("*.tmp",)
    assert config.filesystem.max_files == 42
    assert config.filesystem.native_events is False
    assert config.processes.enabled is False
    assert config.network.poll_interval == pytest.approx(1.5)
    assert config.privacy.capture_environment is False
    assert config.privacy.show_environment is True
    assert config.storage.database == (tmp_path / "data" / "sessions.db").resolve()
    assert config.storage.max_sessions == 5
    assert config.report.default_format == "json"


def test_environment_variable_selects_configuration(tmp_path: Path) -> None:
    path = tmp_path / "custom.toml"
    path.write_text("[report]\nmax_events = 11\n", encoding="utf-8")
    resolved = find_config_path(None, cwd=tmp_path, environment={"CMDSCOPE_CONFIG": str(path)})
    assert resolved == path.resolve()
    config = load_config(cwd=tmp_path, environment={"CMDSCOPE_CONFIG": str(path)})
    assert config.report.max_events == 11


def test_local_file_is_used_when_no_override_exists(tmp_path: Path) -> None:
    local = tmp_path / "cmdscope.toml"
    local.write_text("[report]\nmax_events = 3\n", encoding="utf-8")
    config = load_config(cwd=tmp_path, environment={})
    assert config.source_path == local.resolve()


def test_unknown_keys_produce_warnings(tmp_path: Path) -> None:
    path = tmp_path / "cmdscope.toml"
    path.write_text("[filesystem]\nunknown = 3\n\n[unexpected]\nx = 1\n", encoding="utf-8")
    config = load_config(path, cwd=tmp_path, environment={})
    assert any("filesystem.unknown" in warning for warning in config.warnings)
    assert any("unexpected" in warning for warning in config.warnings)


def test_invalid_types_raise_configuration_error(tmp_path: Path) -> None:
    path = tmp_path / "cmdscope.toml"
    path.write_text('[filesystem]\nwatch = "not-a-list"\n', encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(path, cwd=tmp_path, environment={})


def test_invalid_report_format_raises(tmp_path: Path) -> None:
    path = tmp_path / "cmdscope.toml"
    path.write_text('[report]\nformat = "pdf"\n', encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(path, cwd=tmp_path, environment={})


def test_missing_explicit_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_config(tmp_path / "missing.toml", cwd=tmp_path, environment={})


def test_invalid_toml_raises(tmp_path: Path) -> None:
    path = tmp_path / "cmdscope.toml"
    path.write_text("[filesystem\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(path, cwd=tmp_path, environment={})


def test_watch_roots_default_to_cwd_and_collapse(tmp_path: Path) -> None:
    nested = tmp_path / "outer" / "inner"
    nested.mkdir(parents=True)
    config = Config(base_dir=tmp_path)
    roots = resolve_watch_roots(config, (), cwd=tmp_path)
    assert roots == (tmp_path.resolve(),)
    roots = resolve_watch_roots(config, (str(tmp_path / "outer"), str(nested)), cwd=tmp_path)
    assert roots == ((tmp_path / "outer").resolve(),)


def test_missing_watch_root_raises(tmp_path: Path) -> None:
    config = Config(base_dir=tmp_path)
    with pytest.raises(ConfigurationError):
        resolve_watch_roots(config, (str(tmp_path / "nope"),), cwd=tmp_path)


def test_default_template_is_valid_toml() -> None:
    parsed = tomllib.loads(default_config_text())
    assert parsed["filesystem"]["enabled"] is True
    assert parsed["storage"]["max_sessions"] == 0


def test_config_as_dict_is_json_friendly(tmp_path: Path) -> None:
    payload = config_as_dict(Config(base_dir=tmp_path))
    assert payload["base_dir"] == str(tmp_path)
    assert "filesystem" in payload
    assert payload["storage"]["max_events"] == 100_000
