# Configuration reference

CmdScope works without any configuration file. A TOML file is optional and is resolved in
this order:

1. `--config <path>` on the command line (a missing file is an error).
2. The `CMDSCOPE_CONFIG` environment variable (a missing file is an error).
3. `./cmdscope.toml` in the current directory.
4. `<user config dir>/config.toml`:
   - Windows: `%APPDATA%\cmdscope\config.toml`
   - Linux: `$XDG_CONFIG_HOME/cmdscope/config.toml` (or `~/.config/cmdscope/config.toml`)
   - macOS: `~/Library/Application Support/cmdscope/config.toml`

Use `cmdscope config --path` to print the resolved locations and `cmdscope config --init`
to write a commented template in the current directory.

Relative paths inside a configuration file resolve against the **directory of that file**.
Paths given on the command line resolve against the **current directory**.

Unknown keys are ignored with a warning; invalid values stop the run with a clear error.

## `[filesystem]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `enabled` | bool | `true` | Enable filesystem observation. |
| `watch` | list of strings | `[]` | Watch roots. Empty means the current working directory. |
| `ignore` | list of strings | `.git`, `__pycache__`, … | Glob patterns excluded from observation. |
| `max_files` | int | `100000` | Maximum entries per snapshot. Exceeding it degrades the collector. |
| `poll_interval` | float | `1.0` | Seconds between reconciliation scans in polling mode. |
| `native_events` | bool | `true` | Use OS events when available (falls back to polling). |

Ignore pattern rules:

- A pattern without a slash (`node_modules`, `*.log`) matches a path component anywhere.
- A pattern with a slash (`build/*`) is matched against the POSIX-style relative path.
- Exact paths of CmdScope's own database files are always excluded.

## `[processes]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `enabled` | bool | `true` | Enable process observation. |
| `poll_interval` | float | `0.5` | Seconds between process tree polls. |

## `[network]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `enabled` | bool | `true` | Enable network observation. |
| `poll_interval` | float | `0.25` | Seconds between socket samples. |

## `[privacy]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `capture_environment` | bool | `true` | Store the sanitized initial environment diff. |
| `show_environment` | bool | `false` | Include the environment diff in reports by default. |

## `[storage]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `database` | string | platform data dir | Path of the SQLite database. |
| `max_sessions` | int | `0` | Keep at most N finished sessions (`0` keeps everything). |
| `max_events` | int | `100000` | Maximum events stored per session; excess events are dropped and reported. |

Pruning only removes **finished** sessions and only when `max_sessions` is greater than
zero. The oldest sessions are removed first.

## `[report]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `max_events` | int | `500` | Events shown per section in terminal and HTML reports. |
| `format` | string | `terminal` | Default format for `cmdscope report` (`terminal`, `json`, `html`). |

The JSON report always contains every stored event unless an explicit `--limit` is given.

## Command-line overrides

| Option | Effect |
| --- | --- |
| `--config PATH` | Use a specific configuration file. |
| `--db PATH` | Override the database path. |
| `--watch PATH` | Add a watch root (repeatable). Replaces the configured roots. |
| `--ignore GLOB` | Add an ignore pattern (repeatable). Extends the configured patterns. |
| `--env KEY=VALUE` | Set an environment variable for the child (repeatable). |
| `--no-filesystem`, `--no-processes`, `--no-network` | Disable a collector for this run. |
| `--no-native-events` | Force filesystem polling instead of native OS events. |
| `--include-environment` | Include the sanitized environment diff in the report. |
| `--limit N`, `--all` | Control how many events a report shows. |
| `--no-color` | Disable ANSI colors. |

## Example

```toml
[filesystem]
watch = [".", "../shared-assets"]
ignore = [".git", "node_modules", "*.log", "build/*"]
max_files = 250000

[processes]
poll_interval = 0.25

[network]
poll_interval = 0.5

[storage]
max_sessions = 50

[report]
max_events = 200
format = "html"
```
