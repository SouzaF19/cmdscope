"""Ignore rules for filesystem observation."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

DEFAULT_IGNORE_PATTERNS: tuple[str, ...] = (
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".DS_Store",
    "Thumbs.db",
    "*.pyc",
    "*.pyo",
)


@dataclass(frozen=True, slots=True)
class IgnoreRules:
    """Glob-based exclusions evaluated against a path and its root.

    A pattern without a slash matches a path component anywhere in the tree.
    A pattern with a slash is matched against the POSIX-style relative path.
    ``exclude_paths`` removes exact paths owned by CmdScope (for example its
    own SQLite database and WAL files) even when the watch root contains them.
    """

    patterns: tuple[str, ...] = DEFAULT_IGNORE_PATTERNS
    exclude_paths: tuple[Path, ...] = ()

    def matches(self, path: Path, root: Path) -> bool:
        if path == root:
            return False
        for excluded in self.exclude_paths:
            if path == excluded:
                return True
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            relative = path.name
        parts = relative.split("/")
        for raw_pattern in self.patterns:
            pattern = raw_pattern.strip().strip("/")
            if not pattern:
                continue
            if "/" not in pattern and pattern in parts:
                return True
            if fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(path.name, pattern):
                return True
        return False
