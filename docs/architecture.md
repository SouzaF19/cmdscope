# Architecture

CmdScope separates **collection**, **storage**, **analysis**, and **presentation**. Each
layer depends only on the layers below it, so a new collector never touches SQL or report
formatting, and a new report never touches the operating system.

```text
                ┌──────────────────────────────┐
                │            cli.py             │
                └───────────────┬──────────────┘
                                │
                ┌───────────────▼──────────────┐
                │         core/engine.py        │
                │  session lifecycle + runner   │
                └───┬──────────┬───────────┬────┘
                    │          │           │
        ┌───────────▼──┐  ┌────▼─────┐  ┌──▼────────────┐
        │  collectors/ │  │ platforms│  │   privacy.py  │
        │ fs/proc/net  │  │  caps    │  │  redaction    │
        └───────┬──────┘  └──────────┘  └───────────────┘
                │  core/events.py (typed events)
                ▼
        ┌──────────────────────────────┐
        │  storage/sqlite.py + writer  │
        └───────────────┬──────────────┘
                        ▼
                ┌───────────────┐
                │   analysis/   │  summary.py, comparison.py
                └───────┬───────┘
                        ▼
                ┌───────────────┐
                │   reports/    │  terminal.py, json_report.py, html.py
                └───────────────┘
```

## Modules

| Module | Responsibility |
| --- | --- |
| `cmdscope.core.events` | `EventKind`, `Event`, `StoredEvent`, `CollectorStatus`, `CollectorState` |
| `cmdscope.core.session` | `Session`, `SessionSummary`, `RunResult`, session reference helpers |
| `cmdscope.core.collector` | `Collector` protocol and the immutable `CollectorContext` |
| `cmdscope.core.registry` | Thread-safe PID registry shared by process and network collectors |
| `cmdscope.core.ignore` | Glob and exact-path exclusions for filesystem observation |
| `cmdscope.core.engine` | `RunOptions` and `execute()`: the session runner |
| `cmdscope.collectors.filesystem` | Baseline snapshot, native events, polling fallback, reconciliation |
| `cmdscope.collectors.processes` | psutil-based process tree discovery |
| `cmdsscope.collectors.network` | psutil socket sampling attributed through the PID registry |
| `cmdscope.platforms` | Per-OS capabilities, data directories, command launch rules |
| `cmdscope.storage.sqlite` | Schema, sessions, events, collector status, pruning |
| `cmdscope.storage.writer` | Single writer thread that serializes events into SQLite |
| `cmdscope.analysis.summary` | Counts and warnings derived from stored events |
| `cmdscope.analysis.comparison` | Session diffing by normalized event signature |
| `cmdscope.reports.*` | Terminal, JSON, and HTML rendering |
| `cmdscope.privacy` | Redaction of command lines and environment values |
| `cmdscope.config` | TOML loading, validation, path resolution |
| `cmdscope.diagnostics` | `cmdscope doctor` implementation |

## Event model

Every observation is a typed event:

```python
Event(
    kind=EventKind.FILESYSTEM_CREATED,
    occurred_at=datetime(...),      # always timezone-aware UTC
    source="filesystem",
    payload={"path": "...", "size": 12, "is_dir": False, "root": "..."},
)
```

Event kinds:

| Kind | Meaning |
| --- | --- |
| `filesystem.created` / `.modified` / `.deleted` | Metadata change under a watch root |
| `process.started` | A process was observed for the first time |
| `process.finished` | A previously observed process is no longer visible |
| `process.still_running` | A process was still alive when the session ended |
| `network.connection` | A TCP/UDP endpoint attributed to a session process |
| `session.warning` | Reserved for session-level warnings |

Payloads are plain JSON so they can be stored, exported, and compared without custom
serializers.

## Session lifecycle

1. `RunOptions` is resolved from the configuration file and the command line.
2. The database is opened and a session row is created.
3. The event writer thread starts (collectors only enqueue events).
4. The filesystem collector takes its **baseline snapshot** — before the command starts.
5. The command is launched with the user's environment plus `--env` overrides.
6. The process and network collectors start and register the root PID.
7. CmdScope waits for the child, forwarding interrupts to the process tree.
8. Collectors stop: the filesystem collector performs a final reconciliation, the process
   collector reports survivors, the network collector takes a last sample.
9. The event writer is flushed and closed, collector statuses are persisted, and the
   session row is finalized (status, exit code, duration).
10. Optional pruning removes the oldest finished sessions when `storage.max_sessions` is set.

If the command cannot be launched, the session is still recorded with status `failed` and
the error message; the CLI prints the session reference so the failure can be inspected.

## Concurrency model

- The child process runs in its own process group (`start_new_session=True` on POSIX,
  `CREATE_NEW_PROCESS_GROUP` on Windows) so an interrupt can be forwarded to the tree.
- Each collector owns one background thread and communicates only through the event
  callback.
- The event callback puts events on an unbounded queue; a single writer thread owns the
  SQLite connection used for event insertion. Collectors never block on the database.
- Collector status is stored after the writer is closed, so status writes never race with
  event writes.

## Storage schema

```text
meta(key, value)                       -- schema version
sessions(id, status, command_json, command_display, cwd, platform_name,
         created_at, started_at, ended_at, duration_s, exit_code,
         interrupted, error, env_diff_json, options_json)
events(id, session_id, kind, occurred_at, source, payload_json)
collectors(session_id, name, state, message, details_json, started_at, stopped_at)
```

Deleting a session cascades to its events and collector rows. The schema version is stored
in `meta`; a database written by a newer CmdScope is refused with a clear error.

## Design decisions

- **No `shell=True`.** Commands are executed as argument vectors. The single exception is
  the documented Windows batch shim path (`.cmd`/`.bat`), which `CreateProcess` cannot run
  directly; CmdScope launches those through `cmd.exe /d /s /c` with
  `subprocess.list2cmdline` quoting.
- **Snapshot plus events, not events alone.** The baseline snapshot makes "created" vs.
  "modified" decidable and lets the final reconciliation recover changes that the OS event
  stream missed.
- **Honest degradation.** When a snapshot is truncated or a collector cannot access a
  resource, the collector reports `degraded`/`failed` instead of guessing.
- **Storage as the boundary.** Analysis and reporting work on persisted events, so a
  report can be regenerated months later from the database alone.
