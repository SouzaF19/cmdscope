# AGENTS.md

Permanent engineering rules for everyone (human or agent) working on CmdScope.

## Mission

CmdScope runs a user-supplied command and records observable changes made during that
session: filesystem changes inside explicitly configured roots, the process tree, and
network endpoints. It produces local, reproducible reports.

CmdScope is an observability tool. It is **not** a sandbox, an antivirus, a surveillance
tool, or a privileged tracing system.

## Non-negotiable product rules

1. Never fabricate, infer as fact, or silently fill in events. Every event in a report
   must come from an observation, a snapshot diff, or a documented polling approximation.
2. State limitations in the output. Do not claim platform coverage that was not tested.
3. Never upload data. There is no telemetry, analytics, crash reporting, or remote API.
4. Never capture file contents. Record metadata only (path, size, mtime, kind).
5. Sanitize command lines and environment variables before persistence. Sensitive values
   must never reach the database, reports, logs, or terminal output.
6. Never elevate privileges automatically and never install drivers or kernel components.
   `cmdscope doctor` may report whether the current process is elevated; nothing more.
7. The monitored command runs with the invoking user's normal permissions. CmdScope must
   not imply isolation, sandboxing, or access control.
8. Do not modify monitored files or execute extra commands to enrich a session. The only
   additional process is the platform's batch shim on Windows (`.cmd`/`.bat`), which is
   required to launch the user's own command and is documented.
9. Do not silently swallow collector errors. A failed or degraded collector is recorded
   in the session and surfaced in reports.
10. Ask for explicit authorization before publishing to PyPI, deleting user data, or
    performing destructive/global machine changes.

## Architecture invariants

- `core/` owns event models, session lifecycle, collector interfaces, and the runner.
- `collectors/` only observe and emit typed events. They do not write reports or SQL.
- `storage/` owns SQLite. It does not know about terminals or HTML.
- `analysis/` reads sessions/events and computes summaries/comparisons. No I/O side effects.
- `reports/` renders data. It does not query the database directly.
- `platforms/` owns OS capability detection, data/config directories, and command launch
  details. Collectors use platform-neutral interfaces.
- Dependencies flow inward. `reports` may import analysis/models; `storage` may import
  core models; core must not import CLI, reports, or collectors.
- New collectors implement `core.collector.Collector`, emit `core.events.Event`, report a
  `CollectorStatus`, and document their observation window and blind spots.
- Events are structured (`EventKind` + JSON payload) and timezone-aware (UTC).

## Code quality

- Python 3.11+. Public APIs have type hints and docstrings.
- Keep functions small and modules focused. No dead code, no placeholder features.
- Prefer stdlib. Add a dependency only when it removes meaningful platform complexity.
- All subprocess execution is argument-based. `shell=True` is forbidden. The only Windows
  batch-shim path is explicit, quoted, and tested.
- Validate and escape all output. SQL must use parameters. HTML must escape user data.
- Errors are explicit: raise `CmdScopeError` subclasses at boundaries, degrade gracefully
  inside collectors, and record the degradation.
- Privacy checks belong in `privacy.py`, not scattered through collectors.

## Testing rules

- Every behavior change needs a test. Tests must run, not be skipped.
- Tests use `tmp_path`; they never write to the user's home, real database, or monitored
  system directories.
- Cover failure paths: missing command, permission errors, truncated scans, event limits,
  unavailable observers, malformed configuration, and invalid session references.
- Integration tests may spawn short-lived local processes and loopback sockets. They must
  clean up and use bounded timeouts.
- Run `ruff check`, `ruff format --check`, `mypy`, and `pytest` before committing.
- Do not weaken assertions or suppress errors to make a test pass.

## Documentation and releases

- README claims must match tested behavior. Mark unvalidated platforms as experimental.
- Update `CHANGELOG.md` for user-visible changes.
- Versioning follows Semantic Versioning. Do not tag or publish without authorization.
- CI must pass on every supported platform before a release.
