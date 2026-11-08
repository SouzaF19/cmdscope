"""Tests for redaction and environment diffing."""

from __future__ import annotations

from cmdscope.privacy import (
    REDACTED,
    environment_diff,
    format_command,
    is_sensitive_name,
    sanitize_command,
    sanitize_environment,
)


def test_sensitive_names_are_detected() -> None:
    assert is_sensitive_name("GITHUB_TOKEN")
    assert is_sensitive_name("api-key")
    assert is_sensitive_name("AWS_SECRET_ACCESS_KEY")
    assert is_sensitive_name("clientSecret")
    assert not is_sensitive_name("PATH")
    assert not is_sensitive_name("LANG")


def test_sanitize_command_redacts_flag_values() -> None:
    sanitized = sanitize_command(
        ["app", "--token", "abc123", "--password=hunter2", "--port", "5432"]
    )
    assert sanitized == ("app", "--token", REDACTED, f"--password={REDACTED}", "--port", "5432")
    assert "abc123" not in " ".join(sanitized)
    assert "hunter2" not in " ".join(sanitized)


def test_sanitize_command_redacts_environment_assignments() -> None:
    sanitized = sanitize_command(["API_KEY=super-secret", "MODE=fast"])
    assert sanitized == (f"API_KEY={REDACTED}", "MODE=fast")


def test_sanitize_command_redacts_url_credentials_and_headers() -> None:
    sanitized = sanitize_command(
        [
            "https://user:pass@example.com/path",
            "Authorization: Bearer abc.def.ghi",
        ]
    )
    joined = " ".join(sanitized)
    assert "pass" not in joined
    assert "abc.def.ghi" not in joined
    assert REDACTED in joined


def test_sanitize_command_redacts_windows_style_switch() -> None:
    sanitized = sanitize_command(["/password:secret"])
    assert sanitized == (f"/password:{REDACTED}",)


def test_sanitize_environment_keeps_ordinary_values() -> None:
    sanitized = sanitize_environment(
        {"PATH": "/usr/bin", "MY_TOKEN": "secret", "HOME": "/home/user"}
    )
    assert sanitized["PATH"] == "/usr/bin"
    assert sanitized["MY_TOKEN"] == REDACTED
    assert sanitized["HOME"] == "/home/user"


def test_environment_diff_reports_changes_with_sanitized_values() -> None:
    diff = environment_diff(
        {"KEEP": "1", "CHANGE": "old", "REMOVE": "x"},
        {"KEEP": "1", "CHANGE": "new", "ADD": "value", "ADD_TOKEN": "secret"},
    )
    added = diff["added"]
    changed = diff["changed"]
    assert isinstance(added, dict)
    assert isinstance(changed, dict)
    assert added["ADD"] == "value"
    assert added["ADD_TOKEN"] == REDACTED
    assert changed["CHANGE"] == {"before": "old", "after": "new"}
    assert diff["removed"] == ["REMOVE"]


def test_format_command_never_leaks_secret() -> None:
    display = format_command(["app", "--api-key", "topsecret"])
    assert "topsecret" not in display
    assert REDACTED in display
