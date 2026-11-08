# Privacy and security

CmdScope is an observability tool. This document describes exactly what it records, what it
never records, and how the data is protected.

## What is recorded

| Data | Where it comes from | Stored as |
| --- | --- | --- |
| Command line | The arguments you passed | Redacted text |
| Working directory | Your shell | Absolute path |
| Exit code, duration, status | The monitored process | Numbers and status strings |
| File metadata | Filesystem events and snapshots | Path, size, mtime, directory flag |
| Process metadata | psutil | PID, PPID, name, executable path, redacted command line |
| Network endpoints | psutil | Local/remote address and port, protocol, status, owning PID |
| Environment diff | The environment handed to the child | Variable names, redacted values |
| Collector status | CmdScope itself | State, message, counters |

## What is never recorded

- **File contents.** CmdScope never opens, reads, or hashes a monitored file.
- **Command output.** The child's stdout and stderr are inherited from your terminal and
  are not captured or stored.
- **Packet contents.** Only socket endpoints are observed; no payload is inspected.
- **Anything remote.** There is no telemetry, no analytics, no crash reporting, and no
  network client in CmdScope.
- **Hostnames.** Remote addresses are stored as IP addresses; CmdScope never performs a
  reverse DNS lookup.

## Redaction

Command lines and environment variables pass through `cmdscope/privacy.py` before they are
written to the database, a report, or the terminal.

A name is considered sensitive when it contains any of these markers (comparison ignores
punctuation and case):

```text
password, passwd, secret, token, credential, apikey, accesskey,
privatekey, clientsecret, authorization, cookie, session
```

Examples:

| Input | Stored |
| --- | --- |
| `--token abc` | `--token [REDACTED]` |
| `--password=hunter2` | `--password=[REDACTED]` |
| `API_KEY=abc` | `API_KEY=[REDACTED]` |
| `https://user:pass@host/` | `https://user:[REDACTED]@host/` |
| `Authorization: Bearer abc` | `Authorization: [REDACTED]` |
| `MY_TOKEN` (environment) | `[REDACTED]` |

Redaction is name-based. A secret stored under an innocuous name (for example
`MY_SETTING=ghp_...`) is **not** detected. Keep secrets in a dedicated variable name or in
a secret manager, and prefer passing them through the environment rather than the command
line.

The environment diff is stored even when values are redacted, so you can see *which*
variable changed without seeing the secret.

## Local storage

- Sessions are stored in a SQLite database in the per-user data directory.
- On POSIX systems the database file is created with mode `0600` and its directory with
  `0700`.
- The database is never uploaded. Copying it elsewhere is a manual action.
- `storage.max_sessions` can limit how many sessions are kept; pruning only removes
  finished sessions and only when you configure it.

## Privileges

- CmdScope runs with your permissions and never elevates.
- `cmdscope doctor` reports whether the current process happens to be elevated; it does
  not change anything.
- The monitored command inherits your permissions. CmdScope does not sandbox it, does not
  restrict its syscalls, and does not isolate its network access.
- On Windows, batch files (`.cmd`/`.bat`) are launched through `cmd.exe /d /s /c` because
  `CreateProcess` cannot execute them directly. This is the only shell indirection in the
  project, and it is applied to your own command.

## Threat model

CmdScope assumes the local machine is not hostile. It protects your data from *accidental
disclosure* (reports, logs, shared screens, copied files) and from *unintended collection*
(contents, output, payloads). It does not defend against an attacker who already has your
user privileges.

## Reporting a vulnerability

Please report security issues privately through GitHub Security Advisories on the
repository page, or by opening an issue that contains no sensitive data. See
[SECURITY.md](../SECURITY.md).
