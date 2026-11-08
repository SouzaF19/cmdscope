"""Command-line interface for CmdScope."""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
import traceback
from collections.abc import Sequence
from pathlib import Path

from cmdscope import __version__
from cmdscope.analysis.comparison import compare_sessions
from cmdscope.analysis.summary import summarize
from cmdscope.config import (
    Config,
    config_as_dict,
    default_config_text,
    load_config,
    resolve_watch_roots,
    user_config_path,
)
from cmdscope.core.engine import RunOptions, execute
from cmdscope.core.events import StoredEvent
from cmdscope.core.session import Session, SessionSummary
from cmdscope.diagnostics import collect_diagnostics, render_diagnostics
from cmdscope.errors import (
    CmdScopeError,
    CommandLaunchError,
    ConfigurationError,
    ReportExistsError,
    SessionNotFoundError,
)
from cmdscope.reports.html import render_html
from cmdscope.reports.json_report import (
    build_report,
    render_comparison_json,
    render_json,
)
from cmdscope.reports.terminal import (
    render_comparison,
    render_session_report,
    render_sessions,
)
from cmdscope.storage.sqlite import Storage


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser. Kept public for tests and documentation."""

    parser = argparse.ArgumentParser(
        prog="cmdscope",
        description=(
            "Run a command and record observable filesystem, process, and network "
            "changes. Local, privacy-first, and honest about limitations."
        ),
    )
    parser.add_argument("--version", action="version", version=f"cmdscope {__version__}")
    parser.add_argument(
        "--config",
        type=Path,
        metavar="PATH",
        help="explicit TOML configuration file",
    )
    parser.add_argument(
        "--db",
        type=Path,
        metavar="PATH",
        help="override the session database path",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="disable ANSI colors",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="show debug tracebacks for unexpected errors",
    )

    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="run and observe a command",
        description="Run a command with observation. Use '--' before the command.",
    )
    run_parser.add_argument(
        "--watch",
        action="append",
        default=[],
        metavar="PATH",
        help="additional watch root (repeatable)",
    )
    run_parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="GLOB",
        help="additional ignore pattern (repeatable)",
    )
    run_parser.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="set an environment variable for the monitored command (repeatable)",
    )
    run_parser.add_argument(
        "--no-filesystem",
        action="store_true",
        help="disable filesystem observation",
    )
    run_parser.add_argument(
        "--no-processes",
        action="store_true",
        help="disable process observation",
    )
    run_parser.add_argument(
        "--no-network",
        action="store_true",
        help="disable network observation",
    )
    run_parser.add_argument(
        "--no-report",
        action="store_true",
        help="do not print a report after the command",
    )
    run_parser.add_argument(
        "--json",
        action="store_true",
        help="print the post-run report as JSON",
    )
    run_parser.add_argument(
        "--include-environment",
        action="store_true",
        help="include the sanitized initial environment diff in the report",
    )
    run_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="maximum events shown per section in the terminal report",
    )
    run_parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="command and arguments (use '--' before the command)",
    )

    sessions_parser = subparsers.add_parser("sessions", help="list recorded sessions")
    sessions_parser.add_argument("--limit", type=int, default=50, metavar="N")
    sessions_parser.add_argument("--json", action="store_true")

    show_parser = subparsers.add_parser("show", help="show one session")
    show_parser.add_argument("session", help="session reference (S000001 or 1)")
    show_parser.add_argument("--limit", type=int, default=None, metavar="N")
    show_parser.add_argument("--all", action="store_true", help="show every event")
    show_parser.add_argument("--include-environment", action="store_true")
    show_parser.add_argument("--json", action="store_true")

    report_parser = subparsers.add_parser("report", help="render a session report")
    report_parser.add_argument("session", help="session reference (S000001 or 1)")
    report_parser.add_argument(
        "--format",
        choices=("terminal", "json", "html"),
        default=None,
        help="report format (default from configuration)",
    )
    report_parser.add_argument("--output", type=Path, metavar="PATH", help="write to a file")
    report_parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing output file",
    )
    report_parser.add_argument("--limit", type=int, default=None, metavar="N")
    report_parser.add_argument("--all", action="store_true", help="include every event")
    report_parser.add_argument("--include-environment", action="store_true")

    compare_parser = subparsers.add_parser("compare", help="compare two sessions")
    compare_parser.add_argument("session_a")
    compare_parser.add_argument("session_b")
    compare_parser.add_argument(
        "--format",
        choices=("terminal", "json"),
        default="terminal",
    )
    compare_parser.add_argument("--limit", type=int, default=None, metavar="N")
    compare_parser.add_argument("--all", action="store_true")

    doctor_parser = subparsers.add_parser("doctor", help="inspect local capabilities")
    doctor_parser.add_argument("--json", action="store_true")

    config_parser = subparsers.add_parser("config", help="show or initialize configuration")
    config_parser.add_argument("--path", action="store_true", help="show configuration paths")
    config_parser.add_argument("--json", action="store_true", help="show effective values")
    config_parser.add_argument(
        "--init",
        action="store_true",
        help="write a commented cmdscope.toml in the current directory",
    )
    config_parser.add_argument("--force", action="store_true", help="overwrite on --init")

    subparsers.add_parser("version", help="print the version")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""

    _configure_console()
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="cmdscope: %(levelname)s: %(message)s",
    )

    try:
        return _dispatch(args, parser)
    except ConfigurationError as exc:
        print(f"cmdscope: configuration error: {exc}", file=sys.stderr)
        return 2
    except CommandLaunchError as exc:
        print(f"cmdscope: error: {exc}", file=sys.stderr)
        if exc.session_id is not None:
            print(
                f"cmdscope: partial session S{exc.session_id:06d} was recorded; "
                f"inspect it with: cmdscope show S{exc.session_id:06d}",
                file=sys.stderr,
            )
        return exc.exit_code
    except SessionNotFoundError as exc:
        print(f"cmdscope: {exc}", file=sys.stderr)
        return 1
    except CmdScopeError as exc:
        print(f"cmdscope: error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("cmdscope: interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"cmdscope: internal error: {exc}", file=sys.stderr)
        if args.verbose or os.environ.get("CMDSCOPE_DEBUG"):
            traceback.print_exc()
        return 1


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(OSError, ValueError):
                reconfigure(errors="replace")


def _dispatch(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.subcommand == "version":
        print(f"cmdscope {__version__}")
        return 0

    config = load_config(args.config, cwd=Path.cwd())
    database = _database_path(args, config)

    if args.subcommand == "run":
        return _run(args, parser, config, database)
    if args.subcommand == "sessions":
        return _sessions(args, config, database)
    if args.subcommand == "show":
        return _show(args, config, database)
    if args.subcommand == "report":
        return _report(args, config, database)
    if args.subcommand == "compare":
        return _compare(args, config, database)
    if args.subcommand == "doctor":
        return _doctor(args, config, database)
    if args.subcommand == "config":
        return _config(args, config)
    parser.error(f"unknown subcommand: {args.subcommand}")


def _database_path(args: argparse.Namespace, config: Config) -> Path:
    if args.db is not None:
        candidate = Path(args.db).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        return candidate.resolve()
    return config.database_path()


def _color_enabled(args: argparse.Namespace) -> bool:
    if args.no_color or os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def _validate_limit(value: int | None) -> None:
    if value is not None and value < 1:
        raise ConfigurationError("--limit must be at least 1")


def _parse_env(values: list[str], parser: argparse.ArgumentParser) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for item in values:
        key, separator, value = item.partition("=")
        if not separator or not key:
            parser.error("--env requires KEY=VALUE")
        overrides[key] = value
    return overrides


def _run(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    config: Config,
    database: Path,
) -> int:
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("run requires a command, for example: cmdscope run -- npm install")
    _validate_limit(args.limit)

    cwd = Path.cwd()
    watch_roots = resolve_watch_roots(config, tuple(args.watch), cwd=cwd)
    ignore_patterns = tuple(dict.fromkeys((*config.filesystem.ignore, *args.ignore)))
    env_overrides = _parse_env(args.env, parser)

    options = RunOptions(
        command=tuple(command),
        cwd=cwd,
        watch_roots=watch_roots,
        ignore_patterns=ignore_patterns,
        env_overrides=env_overrides,
        database=database,
        enable_filesystem=config.filesystem.enabled and not args.no_filesystem,
        enable_processes=config.processes.enabled and not args.no_processes,
        enable_network=config.network.enabled and not args.no_network,
        filesystem_poll_interval=config.filesystem.poll_interval,
        process_poll_interval=config.processes.poll_interval,
        network_poll_interval=config.network.poll_interval,
        max_files=config.filesystem.max_files,
        max_events=config.storage.max_events,
        max_sessions=config.storage.max_sessions,
        use_native_events=config.filesystem.native_events,
        capture_environment=config.privacy.capture_environment,
    )

    result = execute(options)
    if not args.no_report:
        session, events, summary = _load_session_data(database, str(result.session_id))
        include_environment = args.include_environment or config.privacy.show_environment
        print()
        if args.json:
            report = build_report(
                session,
                summary,
                events,
                include_environment=include_environment,
            )
            print(render_json(report), end="")
        else:
            limit = args.limit if args.limit is not None else config.report.max_events
            text = render_session_report(
                session,
                summary,
                events,
                limit=limit,
                include_environment=include_environment,
                color=_color_enabled(args),
            )
            print(text)
    return 130 if result.interrupted else result.exit_code


def _sessions(args: argparse.Namespace, config: Config, database: Path) -> int:
    if args.limit < 1:
        raise ConfigurationError("--limit must be at least 1")
    with Storage(database) as storage:
        sessions = storage.list_sessions(args.limit)
    if args.json:
        payload = [
            {
                "id": session.id,
                "ref": session.ref,
                "status": str(session.status),
                "command": session.command,
                "command_display": session.command_display,
                "started_at": session.started_at.isoformat() if session.started_at else None,
                "duration_s": session.duration_s,
                "exit_code": session.exit_code,
                "interrupted": session.interrupted,
            }
            for session in sessions
        ]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(render_sessions(sessions, color=_color_enabled(args)))
    return 0


def _load_session_data(
    database: Path, reference: str
) -> tuple[Session, list[StoredEvent], SessionSummary]:
    with Storage(database) as storage:
        session = storage.require_session(reference)
        events = storage.get_events(session.id)
        summary = summarize(session, events)
    return session, events, summary


def _show(args: argparse.Namespace, config: Config, database: Path) -> int:
    _validate_limit(args.limit)
    session, events, summary = _load_session_data(database, args.session)
    if args.json:
        report = build_report(
            session,
            summary,
            events,
            include_environment=args.include_environment or config.privacy.show_environment,
        )
        print(render_json(report), end="")
        return 0
    limit = None if args.all else (args.limit or config.report.max_events)
    print(
        render_session_report(
            session,
            summary,
            events,
            limit=limit,
            include_environment=args.include_environment or config.privacy.show_environment,
            color=_color_enabled(args),
        )
    )
    return 0


def _report(args: argparse.Namespace, config: Config, database: Path) -> int:
    _validate_limit(args.limit)
    session, events, summary = _load_session_data(database, args.session)
    report_format = args.format or config.report.default_format
    include_environment = args.include_environment or config.privacy.show_environment

    if report_format == "terminal":
        limit = None if args.all else (args.limit or config.report.max_events)
        content = (
            render_session_report(
                session,
                summary,
                events,
                limit=limit,
                include_environment=include_environment,
                color=False if args.output else _color_enabled(args),
            )
            + "\n"
        )
    elif report_format == "json":
        event_limit = None if args.all else args.limit
        report = build_report(
            session,
            summary,
            events,
            include_environment=include_environment,
            event_limit=event_limit,
        )
        content = render_json(report)
    else:
        event_limit = None if args.all else (args.limit or config.report.max_events)
        report = build_report(
            session,
            summary,
            events,
            include_environment=include_environment,
            event_limit=event_limit,
        )
        content = render_html(report)

    if args.output is None:
        print(content, end="" if content.endswith("\n") else "\n")
        return 0
    _write_output(args.output, content, force=args.force)
    print(f"Report written to {args.output}")
    return 0


def _write_output(path: Path, content: str, *, force: bool) -> None:
    target = path.expanduser()
    if target.exists() and not force:
        raise ReportExistsError(f"output file already exists: {target} (use --force to overwrite)")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise CmdScopeError(f"cannot write report {target}: {exc}") from exc


def _compare(args: argparse.Namespace, config: Config, database: Path) -> int:
    _validate_limit(args.limit)
    with Storage(database) as storage:
        result = compare_sessions(storage, args.session_a, args.session_b)
    if args.format == "json":
        print(render_comparison_json(result), end="")
        return 0
    limit = None if args.all else (args.limit or config.report.max_events)
    print(render_comparison(result, limit=limit, color=_color_enabled(args)))
    return 0


def _doctor(args: argparse.Namespace, config: Config, database: Path) -> int:
    data = collect_diagnostics(config, database)
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(render_diagnostics(data))
    return 0


def _config(args: argparse.Namespace, config: Config) -> int:
    if args.init:
        target = Path.cwd() / "cmdscope.toml"
        if target.exists() and not args.force:
            raise ConfigurationError(
                f"configuration file already exists: {target} (use --force to overwrite)"
            )
        try:
            target.write_text(default_config_text(), encoding="utf-8")
        except OSError as exc:
            raise ConfigurationError(f"cannot write {target}: {exc}") from exc
        print(f"Wrote {target}")
        return 0

    if args.path:
        print(f"source:     {config.source_path or 'none (defaults)'}")
        print(f"local:      {Path.cwd() / 'cmdscope.toml'}")
        print(f"user:       {user_config_path()}")
        print(f"database:   {config.database_path()}")
        return 0

    print(json.dumps(config_as_dict(config), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
