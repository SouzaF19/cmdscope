"""Domain exceptions with messages suitable for CLI users."""

from __future__ import annotations


class CmdScopeError(Exception):
    """Base class for expected CmdScope failures."""


class ConfigurationError(CmdScopeError):
    """The configuration file or command-line configuration is invalid."""


class StorageError(CmdScopeError):
    """The local session database cannot be opened or updated."""


class SessionNotFoundError(CmdScopeError):
    """A requested session does not exist."""


class ReportExistsError(CmdScopeError):
    """A report output path already exists and was not explicitly overwritten."""


class CommandLaunchError(CmdScopeError):
    """The monitored command could not be started."""

    def __init__(self, message: str, *, exit_code: int = 1, session_id: int | None = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.session_id = session_id


class EventLimitReached(Exception):
    """Internal signal used by the event writer when a session reaches its limit."""
