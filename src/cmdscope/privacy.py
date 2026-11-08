"""Sanitization and environment-diff helpers.

CmdScope stores metadata, never file contents. Command arguments and environment
values are still potentially sensitive, so every value passes through this module
before it reaches SQLite, a report, or a log.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from collections.abc import Mapping

from cmdscope.core.events import JSONValue

REDACTED = "[REDACTED]"

# Normalized names are compared without punctuation, so AWS_SECRET_ACCESS_KEY,
# --api-key and clientSecret all match.
SENSITIVE_MARKERS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "apikey",
    "accesskey",
    "privatekey",
    "clientsecret",
    "authorization",
    "cookie",
    "session",
)

_SENSITIVE_FLAG_RE = re.compile(r"^[-/]{1,2}[A-Za-z0-9_.-]+$")
_URL_CREDENTIALS_RE = re.compile(r"(://[^/\s:@]+:)([^/\s@]+)(@)")
_AUTHORIZATION_RE = re.compile(r"(?i)(authorization\s*:\s*(?:bearer|basic|token)?\s*)\S+")
_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/-]+=*")
_SENSITIVE_QUERY_RE = re.compile(
    r"(?i)([?&](?:token|api_key|apikey|access_token|secret|password)=)[^&\s]+"
)


def is_sensitive_name(name: str) -> bool:
    """Return whether a flag or environment-variable name looks sensitive."""

    normalized = re.sub(r"[^a-z0-9]", "", name.lower())
    return any(marker in normalized for marker in SENSITIVE_MARKERS)


def sanitize_env_value(name: str, value: str) -> str:
    """Redact a value when its variable name is sensitive."""

    return REDACTED if is_sensitive_name(name) else value


def sanitize_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Return a copy with sensitive values redacted."""

    return {key: sanitize_env_value(key, value) for key, value in environment.items()}


def environment_diff(
    parent: Mapping[str, str],
    child: Mapping[str, str],
) -> dict[str, JSONValue]:
    """Describe added, removed, and changed variables with sanitized values.

    Runtime mutation of an environment block is not observable without native
    tracing. This diff only compares the environment CmdScope was asked to use
    for the child with the environment of the CmdScope process.
    """

    added: dict[str, JSONValue] = {
        key: sanitize_env_value(key, child[key]) for key in sorted(child.keys() - parent.keys())
    }
    removed: list[JSONValue] = [key for key in sorted(parent.keys() - child.keys())]
    changed: dict[str, JSONValue] = {}
    for key in sorted(parent.keys() & child.keys()):
        if parent[key] == child[key]:
            continue
        changed[key] = {
            "before": sanitize_env_value(key, parent[key]),
            "after": sanitize_env_value(key, child[key]),
        }
    return {"added": added, "removed": removed, "changed": changed}


def _sanitize_embedded(value: str) -> str:
    """Redact credentials embedded inside an argument (URLs, headers, queries)."""

    sanitized = _URL_CREDENTIALS_RE.sub(rf"\1{REDACTED}\3", value)
    sanitized = _AUTHORIZATION_RE.sub(rf"\1{REDACTED}", sanitized)
    sanitized = _BEARER_RE.sub(rf"\1 {REDACTED}", sanitized)
    return _SENSITIVE_QUERY_RE.sub(rf"\1{REDACTED}", sanitized)


def sanitize_command(argv: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Redact values of sensitive flags, assignments, URLs, and headers."""

    sanitized: list[str] = []
    redact_next = False
    for argument in argv:
        if redact_next:
            sanitized.append(REDACTED)
            redact_next = False
            continue

        handled = False
        if argument.startswith(("-", "/")):
            for separator in ("=", ":"):
                if separator not in argument:
                    continue
                flag, _, _value = argument.partition(separator)
                if is_sensitive_name(flag.lstrip("-/")):
                    sanitized.append(f"{flag}{separator}{REDACTED}")
                    handled = True
                break
            if handled:
                continue
            if _SENSITIVE_FLAG_RE.match(argument) and is_sensitive_name(argument.lstrip("-/")):
                sanitized.append(argument)
                redact_next = True
                continue
        elif "=" in argument:
            name, _, _value = argument.partition("=")
            if is_sensitive_name(name):
                sanitized.append(f"{name}={REDACTED}")
                continue

        sanitized.append(_sanitize_embedded(argument))
    return tuple(sanitized)


def format_command(argv: tuple[str, ...] | list[str]) -> str:
    """Render a sanitized command for display and storage."""

    sanitized = sanitize_command(argv)
    if os.name == "nt":
        return subprocess.list2cmdline(list(sanitized))
    return shlex.join(sanitized)
