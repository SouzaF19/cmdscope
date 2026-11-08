# Contributing to CmdScope

Thanks for considering a contribution. CmdScope is young, so there is room to shape the
design — but the project has non-negotiable rules about honesty, privacy, and safety.

Read [AGENTS.md](AGENTS.md) first: it contains the permanent engineering rules for this
repository (architecture invariants, quality gates, privacy rules).

## Development setup

```console
$ git clone https://github.com/SouzaF19/cmdscope.git
$ cd cmdscope
$ python -m venv .venv
$ .venv\Scripts\activate          # Windows
$ source .venv/bin/activate       # Linux/macOS
$ python -m pip install -e ".[dev]"
```

## Quality gates

Run all of them before opening a pull request:

```console
$ ruff check src tests
$ ruff format --check src tests
$ mypy src/cmdscope
$ pytest
$ pytest --cov=cmdscope --cov-report=term-missing
```

CI runs the same checks on Windows, Ubuntu, and macOS for Python 3.11, 3.12, and 3.13.

## Ground rules

- **No fabricated events.** Every reported value must come from an observation. When a
  collector cannot observe something, say so.
- **No file contents, no telemetry, no uploads.**
- **Never elevate privileges** and never install privileged components.
- **Never hide failures.** A degraded collector must be visible in the report.
- **Tests must run.** Do not skip tests to make a pipeline green.
- **Keep the dependency list small.** A new dependency needs a justification in the pull
  request description.

## Adding a collector

See [docs/collectors.md](docs/collectors.md#writing-a-new-collector). In short: implement
the `Collector` protocol, emit typed events, report an honest status, document the blind
spots, and add tests.

## Pull requests

1. Create a focused branch: `feature/short-description`.
2. Keep the change small enough to review. Separate refactors from behaviour changes.
3. Update `CHANGELOG.md` under "Unreleased" for user-visible changes.
4. Update the README and `docs/` when behaviour or limitations change.
5. Describe in the pull request what you validated and on which platform.

## Commit messages

Use short, imperative summaries, for example:

```text
Add network collector status for access-denied environments
```

## Reporting bugs

Open an issue with:

- the CmdScope version (`cmdscope --version`), Python version, and operating system;
- the exact command you ran and the expected versus observed behaviour;
- the output of `cmdscope doctor` (it contains no personal data);
- a minimal reproduction if possible.

Never paste secrets into an issue — CmdScope redacts its own reports, but your shell
history might not.

## License

Contributions are accepted under the MIT license.
