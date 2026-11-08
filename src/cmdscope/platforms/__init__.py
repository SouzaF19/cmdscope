"""Platform selection.

Only Windows is manually validated for the 0.1 series. Linux and macOS use
the same portable collectors and are exercised by CI.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

from cmdscope.platforms import base
from cmdscope.platforms.base import PlatformInfo

__all__ = [
    "PlatformInfo",
    "config_dir",
    "current_platform_info",
    "data_dir",
    "fallback_info",
    "is_elevated",
    "prepare_command",
]


def current_platform_info(environment: Mapping[str, str] | None = None) -> PlatformInfo:
    """Return capabilities for the host running CmdScope."""

    env = os.environ if environment is None else environment
    platform_key = _platform_key()
    if platform_key.startswith("win"):
        from cmdscope.platforms import windows

        return windows.info(env)
    if platform_key == "darwin":
        from cmdscope.platforms import macos

        return macos.info(env)
    if platform_key.startswith("linux"):
        from cmdscope.platforms import linux

        return linux.info(env)
    return base.fallback_info(env)


def _platform_key() -> str:
    """Indirection keeps static analyzers from folding platform branches."""

    return sys.platform


config_dir = base.config_dir
data_dir = base.data_dir
fallback_info = base.fallback_info
is_elevated = base.is_elevated
prepare_command = base.prepare_command
