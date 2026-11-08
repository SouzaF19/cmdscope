"""CmdScope: local, privacy-first observation of command execution.

CmdScope runs a command and records filesystem, process, and network facts that
are observable during the session. It never captures file contents and never
sends data anywhere.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("cmdscope")
except PackageNotFoundError:  # pragma: no cover - running from a source checkout
    __version__ = "0.0.0+local"

__all__ = ["__version__"]
