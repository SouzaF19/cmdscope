"""Platform-neutral capability and path helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PlatformInfo:
    """Static, honest description of what the current platform can observe."""

    name: str
    display_name: str
    fs_backend: str
    native_filesystem_events: bool
    default_data_dir: Path
    default_config_dir: Path
    notes: tuple[str, ...]

    @property
    def default_database_path(self) -> Path:
        return self.default_data_dir / "cmdscope.db"


def _home(environment: Mapping[str, str], platform_name: str) -> Path:
    keys = ("USERPROFILE", "HOME") if platform_name == "windows" else ("HOME", "USERPROFILE")
    for key in keys:
        value = environment.get(key)
        if value:
            return Path(value).expanduser()
    return Path.home()


def data_dir(platform_name: str, environment: Mapping[str, str] | None = None) -> Path:
    """Return the per-user data directory for *platform_name*.

    The functions accept an environment mapping so tests can validate path
    behavior on any host without touching the real user profile.
    """

    env = os.environ if environment is None else environment
    home = _home(env, platform_name)
    if platform_name == "windows":
        base = env.get("LOCALAPPDATA")
        return (
            Path(base).expanduser() / "cmdscope"
            if base
            else home / "AppData" / "Local" / "cmdscope"
        )
    if platform_name == "macos":
        return home / "Library" / "Application Support" / "cmdscope"
    base = env.get("XDG_DATA_HOME")
    return Path(base).expanduser() / "cmdscope" if base else home / ".local" / "share" / "cmdscope"


def config_dir(platform_name: str, environment: Mapping[str, str] | None = None) -> Path:
    """Return the per-user configuration directory for *platform_name*."""

    env = os.environ if environment is None else environment
    home = _home(env, platform_name)
    if platform_name == "windows":
        base = env.get("APPDATA")
        return (
            Path(base).expanduser() / "cmdscope"
            if base
            else home / "AppData" / "Roaming" / "cmdscope"
        )
    if platform_name == "macos":
        return home / "Library" / "Application Support" / "cmdscope"
    base = env.get("XDG_CONFIG_HOME")
    return Path(base).expanduser() / "cmdscope" if base else home / ".config" / "cmdscope"


def fallback_info(environment: Mapping[str, str] | None = None) -> PlatformInfo:
    """Conservative capabilities for an unrecognized POSIX-like platform."""

    env = os.environ if environment is None else environment
    return PlatformInfo(
        name="unknown",
        display_name=f"Unknown ({sys.platform})",
        fs_backend="polling",
        native_filesystem_events=False,
        default_data_dir=data_dir("linux", env),
        default_config_dir=config_dir("linux", env),
        notes=(
            "This platform is not officially supported. CmdScope uses portable polling.",
            "Only filesystem snapshots inside watch roots are available.",
        ),
    )


def prepare_command(
    argv: Sequence[str],
    environment: Mapping[str, str] | None = None,
) -> list[str]:
    """Prepare *argv* for ``subprocess.Popen`` without using ``shell=True``.

    Windows batch shims (``.cmd``/``.bat``) are not directly executable by
    ``CreateProcess``. For those, CmdScope intentionally launches the user's
    command through ``cmd.exe /c`` using ``subprocess.list2cmdline`` quoting.
    This is the only shell indirection in the project and it is documented.
    """

    if not argv:
        raise ValueError("command must not be empty")

    env = os.environ if environment is None else environment
    if os.name != "nt":
        return list(argv)

    executable = shutil.which(argv[0], path=env.get("PATH")) or argv[0]
    if executable.lower().endswith((".cmd", ".bat")):
        comspec = env.get("COMSPEC") or "cmd.exe"
        return [comspec, "/d", "/s", "/c", subprocess.list2cmdline(list(argv))]
    return list(argv)


def is_elevated() -> bool:
    """Return whether the current process is elevated. Never attempts elevation."""

    if os.name == "nt":
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            return False
    try:
        return os.geteuid() == 0  # type: ignore[attr-defined]
    except AttributeError:  # pragma: no cover - non-POSIX non-Windows host
        return False
