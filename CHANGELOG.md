# Changelog

All notable changes to this project are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet.

## [0.1.0] - 2026-11-08

Initial public release.

### Added

- `cmdscope run` — run a command inside an observation session and print a report.
- Filesystem collector: baseline snapshot, native OS events (Windows, Linux, macOS),
  polling fallback, and final reconciliation. Reports created, modified, and deleted
  entries with metadata only.
- Process collector: psutil-based discovery of the root process and its descendants,
  with redacted command lines and explicit "still running" reporting.
- Network collector: TCP/UDP endpoint sampling attributed to the processes of the
  session, with no payload inspection and no DNS lookups.
- Local SQLite session storage with schema versioning, cascading deletes, event limits,
  and optional pruning of old sessions.
- `cmdscope sessions`, `cmdscope show`, `cmdscope report` (terminal, JSON, HTML), and
  `cmdscope compare`.
- `cmdscope doctor` for read-only capability diagnostics and `cmdscope config` for
  inspecting or initializing the TOML configuration.
- Privacy layer that redacts sensitive flags, environment variables, URL credentials, and
  authorization headers before anything is stored.
- TOML configuration with per-collector settings, watch roots, ignore patterns, storage
  limits, and report options.
- Documentation: README, architecture guide, collector guide, configuration reference,
  privacy model, `AGENTS.md` with the permanent engineering rules.

### Known limitations

- Observation is limited to the configured watch roots; CmdScope is not a syscall tracer.
- Processes and sockets are sampled and very short-lived ones can be missed.
- Runtime environment mutations are not observable.
- Windows is the only platform validated manually in this release; Linux and macOS are
  exercised by CI.

[Unreleased]: https://github.com/SouzaF19/cmdscope/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/SouzaF19/cmdscope/releases/tag/v0.1.0
