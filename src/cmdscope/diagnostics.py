"""Read-only environment diagnostics for ``cmdscope doctor``."""

from __future__ import annotations

import importlib.metadata
import os
import sys
from pathlib import Path
from typing import Any

import psutil

from cmdscope import __version__
from cmdscope.config import Config
from cmdscope.core.session import session_ref
from cmdscope.errors import StorageError
from cmdscope.platforms import current_platform_info, is_elevated
from cmdscope.storage.sqlite import Storage


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


def _watchdog_info() -> dict[str, Any]:
    try:
        from watchdog.observers import Observer

        observer = Observer()
        return {
            "available": True,
            "observer": type(observer).__name__,
            "error": None,
        }
    except Exception as exc:
        return {"available": False, "observer": None, "error": str(exc)}


def _network_sample() -> dict[str, Any]:
    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, PermissionError) as exc:
        return {"available": False, "count": None, "error": str(exc)}
    except OSError as exc:
        return {"available": False, "count": None, "error": str(exc)}
    attributed = sum(1 for connection in connections if getattr(connection, "pid", None))
    return {"available": True, "count": len(connections), "with_pid": attributed, "error": None}


def _database_info(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path),
        "parent_writable": os.access(path.parent, os.W_OK),
        "opened": False,
        "schema_version": None,
        "quick_check": None,
        "size_bytes": None,
        "error": None,
    }
    try:
        with Storage(path) as storage:
            result["opened"] = True
            result["schema_version"] = storage.schema_version()
            result["quick_check"] = storage.quick_check()
            result["size_bytes"] = storage.storage_size_bytes()
    except StorageError as exc:
        result["error"] = str(exc)
    return result


def collect_diagnostics(config: Config, database: Path) -> dict[str, Any]:
    """Collect diagnostics without changing machine configuration."""

    info = current_platform_info()
    inotify_limit: int | None = None
    if info.name == "linux":
        try:
            inotify_limit = int(Path("/proc/sys/fs/inotify/max_user_watches").read_text().strip())
        except (OSError, ValueError):
            inotify_limit = None

    diagnostics: dict[str, Any] = {
        "cmdscope_version": __version__,
        "python_version": sys.version.split()[0],
        "executable": sys.executable,
        "platform": {
            "name": info.name,
            "display_name": info.display_name,
            "filesystem_backend": info.fs_backend,
            "native_filesystem_events": info.native_filesystem_events,
            "data_dir": str(info.default_data_dir),
            "config_dir": str(info.default_config_dir),
            "default_database": str(info.default_database_path),
        },
        "user": {
            "elevated": is_elevated(),
            "note": "CmdScope never elevates privileges automatically.",
        },
        "dependencies": {
            "psutil": _package_version("psutil"),
            "watchdog": _package_version("watchdog"),
        },
        "watchdog": _watchdog_info(),
        "network": _network_sample(),
        "database": _database_info(database),
        "configuration": {
            "source_path": str(config.source_path) if config.source_path else None,
            "warnings": list(config.warnings),
        },
        "inotify_max_user_watches": inotify_limit,
        "limitations": list(info.notes),
    }
    return diagnostics


def render_diagnostics(data: dict[str, Any]) -> str:
    """Render diagnostics as a readable terminal report."""

    platform = data.get("platform", {})
    user = data.get("user", {})
    dependencies = data.get("dependencies", {})
    watchdog = data.get("watchdog", {})
    network = data.get("network", {})
    database = data.get("database", {})
    configuration = data.get("configuration", {})
    lines = [
        f"CMDSCOPE - DIAGNOSTICS (v{data.get('cmdscope_version')})",
        "=" * 70,
        f"Python:          {data.get('python_version')} ({data.get('executable')})",
        f"Platform:        {platform.get('display_name')} [{platform.get('name')}]",
        f"Filesystem:      {platform.get('filesystem_backend')}",
        f"Data directory:  {platform.get('data_dir')}",
        f"Config directory:{platform.get('config_dir')}",
        "",
        f"psutil:          {dependencies.get('psutil')}",
        f"watchdog:        {dependencies.get('watchdog')}",
        f"Observer:        {watchdog.get('observer') or watchdog.get('error')}",
        f"Elevated:        {'yes' if user.get('elevated') else 'no'}",
        "",
        f"Database:        {database.get('path')}",
        f"  opened:        {database.get('opened')}",
        f"  schema:        {database.get('schema_version')}",
        f"  quick check:   {database.get('quick_check')}",
        f"  size (bytes):  {database.get('size_bytes')}",
        f"  parent writable: {database.get('parent_writable')}",
    ]
    if database.get("error"):
        lines.append(f"  error:         {database['error']}")
    lines.extend(
        [
            "",
            f"Network sample:  {network.get('count')} inet sockets "
            f"({network.get('with_pid')} with a PID)",
            f"  available:     {network.get('available')}",
        ]
    )
    if network.get("error"):
        lines.append(f"  error:         {network['error']}")
    if data.get("inotify_max_user_watches") is not None:
        lines.append(f"inotify watches: {data['inotify_max_user_watches']}")
    lines.extend(
        [
            "",
            f"Config source:   {configuration.get('source_path') or 'none (defaults)'}",
        ]
    )
    for warning in configuration.get("warnings", []):
        lines.append(f"  warning:       {warning}")
    lines.extend(["", "LIMITATIONS"])
    for note in data.get("limitations", []):
        lines.append(f"  - {note}")
    lines.append("")
    lines.append("No changes were made to the system. No data was transmitted.")
    return "\n".join(lines)


def session_label(session_id: int) -> str:
    """Convenience for diagnostics callers; kept tiny and tested."""

    return session_ref(session_id)
