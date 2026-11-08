"""Linux platform capabilities."""

from __future__ import annotations

import os
from collections.abc import Mapping

from cmdscope.platforms.base import PlatformInfo, config_dir, data_dir


def info(environment: Mapping[str, str] | None = None) -> PlatformInfo:
    env = os.environ if environment is None else environment
    return PlatformInfo(
        name="linux",
        display_name="Linux",
        fs_backend="inotify (watchdog)",
        native_filesystem_events=True,
        default_data_dir=data_dir("linux", env),
        default_config_dir=config_dir("linux", env),
        notes=(
            "Filesystem events cover only the configured watch roots, recursively.",
            "inotify watch limits can reduce coverage; CmdScope reports the degraded state.",
            "Processes are discovered by polling; very short-lived children can be missed.",
            "Network endpoint ownership for other users may require root privileges.",
            "eBPF, auditd, and ptrace-based syscall tracing are intentionally not used.",
            "CmdScope is not a sandbox and does not restrict the monitored command.",
        ),
    )
