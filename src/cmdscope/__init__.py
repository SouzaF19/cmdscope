"""CmdScope: local, privacy-first observation of command execution.

CmdScope runs a command and records filesystem, process, and network facts that
are observable during the session. It never captures file contents and never
sends data anywhere.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
