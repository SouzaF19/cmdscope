# Security policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.1.x | yes |

## Reporting a vulnerability

Please report security issues privately:

1. Use **GitHub Security Advisories** on the repository page
   (`Security` → `Report a vulnerability`), or
2. open a public issue that contains **no sensitive data** if the problem is a crash or a
   usability bug.

Include the CmdScope version, the Python version, the operating system, and a minimal
reproduction. Please do not include credentials, tokens, or private file contents in the
report — CmdScope redacts its own output, but your shell history might not.

You can expect an acknowledgement within a few days and a fix or mitigation plan once the
report is confirmed.

## Security properties of CmdScope

CmdScope is designed so that:

- it never transmits data;
- it never captures file contents or command output;
- it redacts sensitive command-line arguments and environment values before storage;
- it stores sessions locally in a SQLite database (mode `0600` on POSIX);
- it never elevates privileges and never installs privileged components;
- it executes the monitored command without a shell, with the single documented exception
  of Windows batch shims (`.cmd`/`.bat`), which require `cmd.exe`.

CmdScope explicitly does **not** provide sandboxing, isolation, access control, or
protection against malware. See [docs/privacy.md](docs/privacy.md) for the full model.
