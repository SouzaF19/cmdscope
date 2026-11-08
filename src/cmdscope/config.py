"""Configuration loading, validation, and path resolution."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cmdscope.core.ignore import DEFAULT_IGNORE_PATTERNS
from cmdscope.errors import ConfigurationError
from cmdscope.platforms import current_platform_info

_ALLOWED_TOP_LEVEL = {"filesystem", "processes", "network", "privacy", "storage", "report"}
_ALLOWED_KEYS = {
    "filesystem": {"enabled", "watch", "ignore", "max_files", "poll_interval", "native_events"},
    "processes": {"enabled", "poll_interval"},
    "network": {"enabled", "poll_interval"},
    "privacy": {"capture_environment", "show_environment"},
    "storage": {"database", "max_sessions", "max_events"},
    "report": {"max_events", "format"},
}


@dataclass(frozen=True, slots=True)
class FilesystemConfig:
    enabled: bool = True
    watch: tuple[str, ...] = ()
    ignore: tuple[str, ...] = DEFAULT_IGNORE_PATTERNS
    max_files: int = 100_000
    poll_interval: float = 1.0
    native_events: bool = True


@dataclass(frozen=True, slots=True)
class ProcessesConfig:
    enabled: bool = True
    poll_interval: float = 0.5


@dataclass(frozen=True, slots=True)
class NetworkConfig:
    enabled: bool = True
    poll_interval: float = 0.25


@dataclass(frozen=True, slots=True)
class PrivacyConfig:
    capture_environment: bool = True
    show_environment: bool = False


@dataclass(frozen=True, slots=True)
class StorageConfig:
    database: Path | None = None
    max_sessions: int = 0
    max_events: int = 100_000


@dataclass(frozen=True, slots=True)
class ReportConfig:
    max_events: int = 500
    default_format: str = "terminal"


@dataclass(frozen=True, slots=True)
class Config:
    filesystem: FilesystemConfig = field(default_factory=FilesystemConfig)
    processes: ProcessesConfig = field(default_factory=ProcessesConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    source_path: Path | None = None
    base_dir: Path = field(default_factory=Path.cwd)
    warnings: tuple[str, ...] = ()

    def database_path(self) -> Path:
        """Return the configured database path or the platform default."""

        if self.storage.database is not None:
            return self.storage.database
        return current_platform_info().default_database_path


def user_config_path(environment: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    info = current_platform_info(env)
    return info.default_config_dir / "config.toml"


def find_config_path(
    explicit: Path | None,
    *,
    cwd: Path,
    environment: Mapping[str, str] | None = None,
) -> Path | None:
    """Resolve configuration precedence: explicit, env, local, user."""

    env = os.environ if environment is None else environment
    if explicit is not None:
        candidate = explicit.expanduser()
        if not candidate.is_absolute():
            candidate = cwd / candidate
        if not candidate.is_file():
            raise ConfigurationError(f"configuration file not found: {candidate}")
        return candidate.resolve()

    env_path = env.get("CMDSCOPE_CONFIG")
    if env_path:
        candidate = Path(env_path).expanduser()
        if not candidate.is_absolute():
            candidate = cwd / candidate
        if not candidate.is_file():
            raise ConfigurationError(f"CMDSCOPE_CONFIG points to a missing file: {candidate}")
        return candidate.resolve()

    local = cwd / "cmdscope.toml"
    if local.is_file():
        return local.resolve()

    user = user_config_path(env)
    if user.is_file():
        return user.resolve()
    return None


def _require_table(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"configuration section [{name}] must be a table")
    return value


def _unknown_keys(section: str, table: Mapping[str, Any], warnings: list[str]) -> None:
    for key in sorted(set(table) - _ALLOWED_KEYS[section]):
        warnings.append(f"unknown configuration key ignored: {section}.{key}")


def _bool(table: Mapping[str, Any], key: str, default: bool, section: str) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ConfigurationError(f"{section}.{key} must be true or false")
    return value


def _int(table: Mapping[str, Any], key: str, default: int, section: str) -> int:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{section}.{key} must be an integer")
    return value


def _float(table: Mapping[str, Any], key: str, default: float, section: str) -> float:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{section}.{key} must be a number")
    return float(value)


def _str_list(
    table: Mapping[str, Any], key: str, default: tuple[str, ...], section: str
) -> tuple[str, ...]:
    value = table.get(key, list(default))
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigurationError(f"{section}.{key} must be a list of strings")
    return tuple(value)


def _optional_str(
    table: Mapping[str, Any], key: str, default: str | None, section: str
) -> str | None:
    value = table.get(key, default)
    if value is not None and not isinstance(value, str):
        raise ConfigurationError(f"{section}.{key} must be a string")
    return value


def _parse_filesystem(table: Mapping[str, Any], warnings: list[str]) -> FilesystemConfig:
    _unknown_keys("filesystem", table, warnings)
    config = FilesystemConfig(
        enabled=_bool(table, "enabled", True, "filesystem"),
        watch=_str_list(table, "watch", (), "filesystem"),
        ignore=_str_list(table, "ignore", DEFAULT_IGNORE_PATTERNS, "filesystem"),
        max_files=_int(table, "max_files", 100_000, "filesystem"),
        poll_interval=_float(table, "poll_interval", 1.0, "filesystem"),
        native_events=_bool(table, "native_events", True, "filesystem"),
    )
    if config.max_files < 1:
        raise ConfigurationError("filesystem.max_files must be at least 1")
    if config.poll_interval <= 0:
        raise ConfigurationError("filesystem.poll_interval must be greater than zero")
    return config


def _parse_processes(table: Mapping[str, Any], warnings: list[str]) -> ProcessesConfig:
    _unknown_keys("processes", table, warnings)
    config = ProcessesConfig(
        enabled=_bool(table, "enabled", True, "processes"),
        poll_interval=_float(table, "poll_interval", 0.5, "processes"),
    )
    if config.poll_interval <= 0:
        raise ConfigurationError("processes.poll_interval must be greater than zero")
    return config


def _parse_network(table: Mapping[str, Any], warnings: list[str]) -> NetworkConfig:
    _unknown_keys("network", table, warnings)
    config = NetworkConfig(
        enabled=_bool(table, "enabled", True, "network"),
        poll_interval=_float(table, "poll_interval", 0.25, "network"),
    )
    if config.poll_interval <= 0:
        raise ConfigurationError("network.poll_interval must be greater than zero")
    return config


def _parse_privacy(table: Mapping[str, Any], warnings: list[str]) -> PrivacyConfig:
    _unknown_keys("privacy", table, warnings)
    return PrivacyConfig(
        capture_environment=_bool(table, "capture_environment", True, "privacy"),
        show_environment=_bool(table, "show_environment", False, "privacy"),
    )


def _parse_storage(
    table: Mapping[str, Any],
    warnings: list[str],
    base_dir: Path,
) -> StorageConfig:
    _unknown_keys("storage", table, warnings)
    database_value = _optional_str(table, "database", None, "storage")
    database: Path | None = None
    if database_value:
        database = Path(database_value).expanduser()
        if not database.is_absolute():
            database = base_dir / database
        database = database.resolve()
    config = StorageConfig(
        database=database,
        max_sessions=_int(table, "max_sessions", 0, "storage"),
        max_events=_int(table, "max_events", 100_000, "storage"),
    )
    if config.max_sessions < 0:
        raise ConfigurationError("storage.max_sessions must be zero or greater")
    if config.max_events < 1:
        raise ConfigurationError("storage.max_events must be at least 1")
    return config


def _parse_report(table: Mapping[str, Any], warnings: list[str]) -> ReportConfig:
    _unknown_keys("report", table, warnings)
    format_value = _optional_str(table, "format", "terminal", "report") or "terminal"
    if format_value not in {"terminal", "json", "html"}:
        raise ConfigurationError("report.format must be terminal, json, or html")
    config = ReportConfig(
        max_events=_int(table, "max_events", 500, "report"),
        default_format=format_value,
    )
    if config.max_events < 1:
        raise ConfigurationError("report.max_events must be at least 1")
    return config


def _parse_config(
    data: Mapping[str, Any],
    *,
    source_path: Path | None,
    base_dir: Path,
) -> Config:
    warnings: list[str] = []
    for key in sorted(set(data) - _ALLOWED_TOP_LEVEL):
        warnings.append(f"unknown configuration section ignored: {key}")

    filesystem = _parse_filesystem(
        _require_table(data.get("filesystem", {}), "filesystem"), warnings
    )
    processes = _parse_processes(_require_table(data.get("processes", {}), "processes"), warnings)
    network = _parse_network(_require_table(data.get("network", {}), "network"), warnings)
    privacy = _parse_privacy(_require_table(data.get("privacy", {}), "privacy"), warnings)
    storage = _parse_storage(_require_table(data.get("storage", {}), "storage"), warnings, base_dir)
    report = _parse_report(_require_table(data.get("report", {}), "report"), warnings)

    return Config(
        filesystem=filesystem,
        processes=processes,
        network=network,
        privacy=privacy,
        storage=storage,
        report=report,
        source_path=source_path,
        base_dir=base_dir,
        warnings=tuple(warnings),
    )


def load_config(
    explicit: Path | None = None,
    *,
    cwd: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> Config:
    """Load configuration from the first available location.

    Relative paths inside a configuration file resolve against that file's
    directory. A missing explicit file is an error; no implicit file is fine.
    """

    working_dir = (cwd or Path.cwd()).resolve()
    path = find_config_path(explicit, cwd=working_dir, environment=environment)
    if path is None:
        return Config(base_dir=working_dir)

    try:
        raw = path.read_text(encoding="utf-8")
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"invalid TOML in {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigurationError(f"cannot read configuration file {path}: {exc}") from exc

    return _parse_config(data, source_path=path, base_dir=path.parent)


def resolve_watch_roots(
    config: Config,
    command_line_roots: tuple[str, ...],
    *,
    cwd: Path,
) -> tuple[Path, ...]:
    """Resolve, validate, deduplicate, and collapse watch roots.

    CLI paths are relative to the invocation directory. Paths from a config file
    are relative to the configuration file. Overlapping roots are collapsed to
    the parent so the same event is not observed twice.
    """

    if command_line_roots:
        raw_roots = command_line_roots
        base = cwd
    elif config.filesystem.watch:
        raw_roots = config.filesystem.watch
        base = config.base_dir
    else:
        raw_roots = (".",)
        base = cwd

    resolved: list[Path] = []
    for raw in raw_roots:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = base / candidate
        candidate = candidate.resolve()
        if not candidate.exists():
            raise ConfigurationError(f"watch root does not exist: {candidate}")
        if not candidate.is_dir():
            raise ConfigurationError(f"watch root is not a directory: {candidate}")
        resolved.append(candidate)

    unique: list[Path] = []
    seen: set[str] = set()
    for root in resolved:
        key = os.path.normcase(str(root))
        if key not in seen:
            seen.add(key)
            unique.append(root)

    collapsed: list[Path] = []
    for root in unique:
        if any(root != other and root.is_relative_to(other) for other in unique):
            continue
        collapsed.append(root)
    return tuple(collapsed)


def default_config_text() -> str:
    """Return a commented TOML template for ``cmdscope config --init``."""

    ignored = ", ".join(f'"{pattern}"' for pattern in DEFAULT_IGNORE_PATTERNS)
    return f"""# CmdScope configuration
# All settings are optional. Relative paths resolve against this file.

[filesystem]
enabled = true
# Empty means the current working directory.
watch = []
ignore = [{ignored}]
max_files = 100000
poll_interval = 1.0
# Use native OS events (ReadDirectoryChangesW/inotify/FSEvents) when possible.
native_events = true

[processes]
enabled = true
poll_interval = 0.5

[network]
enabled = true
poll_interval = 0.25

[privacy]
# Store only a sanitized diff of the initial child environment.
capture_environment = true
# Do not include environment keys in reports by default.
show_environment = false

[storage]
# Defaults to the per-user data directory.
# database = "cmdscope.db"
# 0 keeps all sessions.
max_sessions = 0
max_events = 100000

[report]
max_events = 500
format = "terminal"
"""


def config_as_dict(config: Config) -> dict[str, Any]:
    """Return a JSON-friendly representation of the effective configuration."""

    def database(value: Path | None) -> str | None:
        return str(value) if value is not None else None

    return {
        "source_path": str(config.source_path) if config.source_path else None,
        "base_dir": str(config.base_dir),
        "filesystem": {
            "enabled": config.filesystem.enabled,
            "watch": list(config.filesystem.watch),
            "ignore": list(config.filesystem.ignore),
            "max_files": config.filesystem.max_files,
            "poll_interval": config.filesystem.poll_interval,
            "native_events": config.filesystem.native_events,
        },
        "processes": {
            "enabled": config.processes.enabled,
            "poll_interval": config.processes.poll_interval,
        },
        "network": {
            "enabled": config.network.enabled,
            "poll_interval": config.network.poll_interval,
        },
        "privacy": {
            "capture_environment": config.privacy.capture_environment,
            "show_environment": config.privacy.show_environment,
        },
        "storage": {
            "database": database(config.storage.database),
            "effective_database": str(config.database_path()),
            "max_sessions": config.storage.max_sessions,
            "max_events": config.storage.max_events,
        },
        "report": {
            "max_events": config.report.max_events,
            "format": config.report.default_format,
        },
        "warnings": list(config.warnings),
    }
