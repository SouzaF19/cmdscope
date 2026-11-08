# CmdScope

**Run a command and see what it changed — files, processes, and network — locally and privately.**

CmdScope executes a command inside an observation session and records what it can actually
see: files created, modified, or deleted under the directories you watch, the process tree
it spawns, and the network endpoints it opens. When the command finishes, CmdScope prints a
report and stores the session in a local SQLite database so you can compare runs later.

```console
$ cmdscope run -- npm install

CMDSCOPE - EXECUTION REPORT
======================================================================
Session:      S000001
Command:      npm install
Directory:    C:\work\my-project
Platform:     windows
Started:      2026-11-08 10:45:36 UTC
Duration:     12.43 s
Exit status:  0
Status:       completed

FILESYSTEM
  Watch roots: C:\work\my-project
  Created:     184
  Modified:    12
  Deleted:     0
  + node_modules\package\package.json (1.2 KB)
  ~ package-lock.json (48.7 KB)

PROCESSES
  Started:       2
  Finished:      2
  Still running: 0
  [pid 8124] started: node C:\Program Files\nodejs\node_modules\npm\bin\npm-cli.js install

NETWORK
  Connections observed: 1
  [pid 8124] node: 10.0.0.14:52344 -> 104.16.24.35:443 (TCP, ESTABLISHED)

COLLECTORS
  filesystem   ok        backend=native, watch_roots=['C:\\work\\my-project']
  network      ok        connections_observed=1
  processes    ok        observed_processes=4, still_running=0

SUMMARY
  199 events observed in this session.
  196 filesystem changes, 2 processes started, 1 network connection.

LIMITATIONS
  - Only changes under the configured watch roots are observed.
  - Processes and sockets are sampled; short-lived ones can be missed.
  - File contents are never captured.
  - CmdScope observability is not sandboxing or isolation.
  - Runtime environment mutations are not observable externally.

Generated locally by CmdScope. No data was transmitted.
```

> The numbers above are illustrative. Every value in a real report comes from an
> observation made during that session.

## The problem

Installing a package, running a migration, or executing a build script usually leaves you
guessing. `strace` and `Procmon` are powerful but heavy, privileged, platform-specific, and
not session-oriented. CmdScope offers a small, honest, cross-platform tool for the common
question: *"what did that command actually do to my machine?"*

CmdScope is an **observability** tool. It is **not** a sandbox, a permission system, or a
malware detector. The monitored command runs with your normal permissions and is not
restricted in any way.

## Main features

- **Filesystem observation** — metadata changes (created / modified / deleted) under explicit
  watch roots, using native OS events (`ReadDirectoryChangesW`, `inotify`, `FSEvents`) with a
  polling fallback.
- **Process tree observation** — the root process and the descendants it spawns, with
  start/finish observations and command lines.
- **Network observation** — TCP/UDP endpoints attributed to the processes of the session.
- **Session history** — every run is stored in a local SQLite database.
- **Comparison** — `cmdscope compare` diffs two sessions by normalized paths and event
  signatures.
- **Reports** — terminal, JSON, and self-contained HTML (no scripts, no external assets).
- **Privacy by default** — no telemetry, no uploads, no file contents, and automatic
  redaction of sensitive flags and environment variables.
- **Honest capabilities** — every collector reports its state and blind spots in the report.

## Installation

```console
$ pip install cmdscope
```

From a checkout:

```console
$ python -m pip install -e ".[dev]"
$ cmdscope --version
```

Python 3.11 or newer is required.

## Requirements

| Requirement | Notes |
| --- | --- |
| Python | 3.11+ (tested on 3.13) |
| Dependencies | `psutil`, `watchdog` (installed automatically) |
| Privileges | none required; CmdScope never elevates on its own |

The database lives in the per-user data directory:

| Platform | Default database |
| --- | --- |
| Windows | `%LOCALAPPDATA%\cmdscope\cmdscope.db` |
| Linux | `$XDG_DATA_HOME/cmdscope/cmdscope.db` (or `~/.local/share/cmdscope/cmdscope.db`) |
| macOS | `~/Library/Application Support/cmdscope/cmdscope.db` |

Use `cmdscope doctor` to see the resolved paths and the capabilities of your machine.

## Usage

```console
# Observe a command in the current directory
cmdscope run -- npm install

# Watch extra directories (repeatable)
cmdscope run --watch ./src --watch ./config -- python app.py

# Ignore noisy paths
cmdscope run --ignore "*.log" --ignore "node_modules/**" -- pytest -q

# Pass environment variables to the command (values are redacted in reports)
cmdscope run --env API_TOKEN=secret -- node deploy.js

# Skip collectors you do not need
cmdscope run --no-network -- python build.py

# Force polling on network filesystems (CIFS/NFS) where events are unreliable
cmdscope run --no-native-events -- python build.py

# Session history
cmdscope sessions
cmdscope sessions --json

# Inspect one session
cmdscope show S000001
cmdscope show 1 --include-environment

# Reports
cmdscope report S000001
cmdscope report S000001 --format json
cmdscope report S000001 --format html --output report.html

# Compare two runs
cmdscope compare S000001 S000002
cmdscope compare S000001 S000002 --format json

# Environment diagnostics and configuration
cmdscope doctor
cmdscope config --path
cmdscope config --init
```

Global options (`--config`, `--db`, `--no-color`, `--verbose`) must be placed **before** the
subcommand:

```console
cmdscope --db ./local.db run -- python app.py
```

### What is observed, and where

- The **watch root** defaults to the current working directory. Use `--watch` or
  `filesystem.watch` in the configuration file to add others.
- Files are observed **by metadata only**: path, size, modification time, and whether the
  entry is a directory. Contents are never read.
- Events are *observed during the session* in the watched roots. CmdScope does not prove
  causality: another process writing into the same directory at the same time will also
  show up.
- CmdScope's own database files are automatically excluded from the report, even when the
  database lives inside a watch root.

## Supported systems

| Platform | Status in 0.1.x |
| --- | --- |
| Windows 10 / 11 | **Validated manually** on Windows 11 with Python 3.13, plus CI |
| Linux | Implemented and exercised by CI (Ubuntu); not manually validated in this release |
| macOS | Implemented and exercised by CI (macOS); experimental |

The collectors are portable, but the underlying observation mechanisms differ. See
[docs/collectors.md](docs/collectors.md) for the per-platform behaviour and the exact
blind spots of each collector.

## Known limitations

- **Not a sandbox.** CmdScope does not restrict, isolate, or mediate the monitored command.
- **Not syscall tracing.** CmdScope does not use `ptrace`, eBPF, ETW, or a kernel driver. It
  cannot see file access, registry writes, or syscalls that do not change observed state.
- **Limited filesystem coverage.** Only the configured watch roots are observed. Changes
  anywhere else on the system are invisible.
- **Sampling.** Processes and sockets are discovered by polling. A child process or a
  connection that lives entirely between two polls can be missed.
- **Network attribution needs privileges on macOS.** Unprivileged processes cannot see
  socket owners there; CmdScope marks the network collector as `degraded` instead of
  pretending it observed nothing.
- **No runtime environment visibility.** Only the initial environment diff handed to the
  child is recorded. Mutating `environ` inside a running process cannot be observed.
- **No attribution of causes.** The report says what was observed, not which process caused
  it, except for processes that CmdScope itself launched.
- **Partial snapshots.** If a watch root is larger than `filesystem.max_files`, CmdScope
  reports a degraded collector instead of guessing which files were deleted.

## Privacy and security

- All data stays on your machine. CmdScope has no network client, no telemetry, and no
  analytics.
- File contents are never captured.
- Command lines and environment variables are redacted before they are stored
  (`--token`, `--password`, `*_SECRET`, `*_TOKEN`, URL credentials, `Authorization`
  headers, and similar).
- The monitored command runs with your permissions. CmdScope never elevates, never installs
  drivers, and never modifies the files it watches.
- Details: [docs/privacy.md](docs/privacy.md).

## Architecture

```text
cli ──► core.engine ──► collectors ──► core.events (typed events)
                │                              │
                └──────────► storage.sqlite ◄──┘
                                  │
                            analysis ──► reports
```

- `core/` — event models, session lifecycle, collector interface, runner.
- `collectors/` — filesystem, process, and network observation.
- `platforms/` — OS capabilities, data directories, command launch details.
- `storage/` — SQLite persistence and the event writer thread.
- `analysis/` — summaries and session comparison.
- `reports/` — terminal, JSON, and HTML rendering.
- `cli.py` — argument parsing and user-facing behaviour.

See [docs/architecture.md](docs/architecture.md) for details and
[docs/collectors.md](docs/collectors.md) for the guide to adding a new collector.

## Configuration

Optional TOML file, searched in this order: `--config <path>`, `$CMDSCOPE_CONFIG`,
`./cmdscope.toml`, then the per-user config directory.

```toml
[filesystem]
enabled = true
watch = []
ignore = [".git", "__pycache__", "*.pyc"]
max_files = 100000
poll_interval = 1.0
native_events = true

[processes]
enabled = true
poll_interval = 0.5

[network]
enabled = true
poll_interval = 0.25

[privacy]
capture_environment = true
show_environment = false

[storage]
max_sessions = 0
max_events = 100000

[report]
max_events = 500
format = "terminal"
```

Run `cmdscope config --init` to write a commented template. Full reference:
[docs/configuration.md](docs/configuration.md).

## Roadmap

- [x] Session runner with filesystem, process, and network collectors
- [x] Local SQLite history, session comparison, JSON/HTML reports
- [x] Privacy-first sanitization and `cmdscope doctor`
- [ ] Optional ETW/eBPF backends for deeper coverage (opt-in, clearly marked as elevated)
- [ ] Registry and environment-diff collectors on Windows
- [ ] Session diffing in HTML reports
- [ ] Signed releases and reproducible-build verification

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) first; it documents
the engineering rules, quality gates, and the workflow we use to keep CmdScope
dependable.

```console
$ python -m pip install -e ".[dev]"
$ ruff check src tests
$ ruff format --check src tests
$ mypy src/cmdscope
$ pytest
```

## License

MIT — see [LICENSE](LICENSE).
