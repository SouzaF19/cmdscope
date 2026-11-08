"""Thread-safe registry of PIDs observed for the current session."""

from __future__ import annotations

from threading import Lock


class ProcessRegistry:
    """Share the observed process set between process and network collectors.

    Network attribution depends on this registry. A PID that was never observed
    is never attributed to the session, even if it holds a connection.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._names: dict[int, str] = {}

    def add(self, pid: int, name: str) -> None:
        with self._lock:
            self._names[pid] = name

    def remove(self, pid: int) -> None:
        with self._lock:
            self._names.pop(pid, None)

    def snapshot(self) -> dict[int, str]:
        with self._lock:
            return dict(self._names)

    def __len__(self) -> int:
        with self._lock:
            return len(self._names)
