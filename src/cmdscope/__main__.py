"""Allow ``python -m cmdscope``."""

from __future__ import annotations

from cmdscope.cli import main

if __name__ == "__main__":  # pragma: no cover - exercised by the installed entry point
    raise SystemExit(main())
