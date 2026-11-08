"""Windows platform capabilities."""

from __future__ import annotations

import os
from collections.abc import Mapping

from cmdscope.platforms.base import PlatformInfo, config_dir, data_dir


def info(environment: Mapping[str, str] | None = None) -> PlatformInfo:
    env = os.environ if environment is None else environment
    return PlatformInfo(
        name="windows",
        display_name="Windows",
        fs_backend="ReadDirectoryChangesW (watchdog)",
        native_filesystem_events=True,
        default_data_dir=data_dir("windows", env),
        default_config_dir=config_dir("windows", env),
        notes=(
            "Filesystem events cover only the configured watch roots, recursively.",
            "Processes are discovered by polling; very short-lived children can be missed.",
            "Network endpoints come from psutil polling; ownership may be incomplete for "
            "processes owned by other users.",
            "Environment mutations inside a running process cannot be observed externally.",
            "CmdScope is not a sandbox and does not restrict the monitored command.",
        ),
    )
