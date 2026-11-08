# Collectors and platform behaviour

Each collector observes one domain, emits typed events, and reports an honest status
(`ok`, `degraded`, `failed`, `disabled`). The status and its message appear in every
report, so a reader always knows how much of the picture is missing.

## Filesystem collector

**What it observes:** metadata changes (path, size, mtime, directory flag) for entries
under the configured watch roots.

**How it works:**

1. A baseline snapshot is taken *before* the monitored command starts. This is what makes
   "created" distinguishable from "modified".
2. Native events are consumed when available:
   | Platform | Backend |
   | --- | --- |
   | Windows | `ReadDirectoryChangesW` (via watchdog) |
   | Linux | `inotify` (via watchdog) |
   | macOS | `FSEvents`/`kqueue` (via watchdog) |
3. A final reconciliation scans the roots again and reports anything the event stream
   missed.
4. If the native observer cannot be started, the collector falls back to polling and marks
   itself `degraded`.

**Blind spots:**

- Changes outside the watch roots are invisible.
- Directory modifications are not reported (only creation and deletion), because a
  directory's mtime changes whenever any child changes.
- Symbolic links are recorded but never followed.
- Permission errors are counted and surfaced; the affected paths are not invented.
- If the snapshot exceeds `filesystem.max_files`, the collector stops reconciling and
  reports `degraded` — it refuses to infer deletions from a partial scan.
- CmdScope's own database files are always excluded, even inside a watch root.

## Process collector

**What it observes:** the root process and the descendants it spawns.

**How it works:** psutil polling (default every 500 ms). The collector walks the children
of every known process and of the root, and matches PIDs with their creation time so a
reused PID is never attributed to the session. Command lines are redacted before they are
stored.

**Blind spots:**

- A child that starts and exits between two polls can be missed entirely.
- When the root exits, descendants that were re-parented cannot be followed any more.
- Processes of other users may be inaccessible; the collector reports `degraded` when
  access is denied.
- `process.finished` means "no longer observed", not "exited cleanly"; the reason is
  included in the payload.
- Processes that are still alive when the session ends produce `process.still_running`
  instead of a fabricated finish event.

## Network collector

**What it observes:** TCP and UDP endpoints (`inet` family) attributed to processes that
the session already knows.

**How it works:** psutil socket sampling (default every 250 ms) filtered through the
shared PID registry. No packet contents, no DNS lookups, no reverse resolution.

**Blind spots:**

- Sockets that live entirely between two samples are missed.
- Endpoints without a PID cannot be attributed and are skipped.
- Ownership of sockets owned by other users may require elevation on Linux and macOS;
  CmdScope never elevates by itself and reports the degradation instead.
- A connection and its socket share one signature, so a socket that changes state is
  reported once.

## Environment

CmdScope records the **initial environment diff** handed to the child process (the
variables added or changed through `--env`), with sensitive values redacted. Runtime
mutations of `environ` inside a running process cannot be observed without privileged
tracing and are therefore not reported.

## Writing a new collector

1. Create `src/cmdscope/collectors/<name>.py` and implement the `Collector` protocol:

   ```python
   from cmdscope.core.collector import CollectorContext
   from cmdscope.core.events import CollectorState, CollectorStatus, Event, EventKind

   class MyCollector:
       name = "my-collector"

       def start(self, context: CollectorContext) -> None:
           self._emit = context.emit
           ...

       def stop(self) -> CollectorStatus:
           return CollectorStatus(
               name=self.name,
               state=CollectorState.OK,
               details={"observed": 0},
           )
   ```

2. Emit `Event` objects with a stable `EventKind`. Add new kinds to
   `cmdscope.core.events.EventKind` and document their payloads.
3. Never write to the database, the terminal, or the network from a collector. Use
   `context.emit` and report problems through `CollectorStatus`.
4. Document the observation window and the blind spots in `docs/collectors.md` and surface
   them in the status message when coverage is partial.
5. Register the collector in `cmdscope.core.engine.execute()` and add it to the CLI flags
   and the configuration file.
6. Add unit tests that use `tmp_path` and a fake emitter, plus an integration test that
   runs a real child process when the behaviour depends on the operating system.
