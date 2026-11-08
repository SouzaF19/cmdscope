"""Self-contained HTML reports with no external assets or scripts."""

from __future__ import annotations

import html
import json
from typing import Any

_CSS = """
:root { color-scheme: light dark; --bg:#f7f8fa; --panel:#fff; --text:#1c2430;
  --muted:#5b6878; --line:#dfe4ea; --accent:#0b7285; --ok:#2b8a3e; --warn:#b36b00;
  --fail:#c92a2a; --code:#f1f3f5; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#101418; --panel:#171d24; --text:#e7edf3; --muted:#9aa8b6;
    --line:#2b3540; --accent:#4dabf7; --ok:#69db7c; --warn:#ffc078; --fail:#ff8787;
    --code:#0d1116; }
}
* { box-sizing: border-box; }
body { margin:0; padding:2rem; background:var(--bg); color:var(--text);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif; }
main { max-width:1100px; margin:0 auto; }
h1 { margin:0 0 .2rem; font-size:1.7rem; letter-spacing:-.02em; }
h2 { margin:2rem 0 .7rem; font-size:1.05rem; text-transform:uppercase;
  letter-spacing:.08em; color:var(--muted); }
.sub { color:var(--muted); margin:0 0 1.5rem; }
.panel { background:var(--panel); border:1px solid var(--line); border-radius:10px;
  padding:1rem 1.2rem; margin:.8rem 0; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:.7rem; margin:1rem 0; }
.card { background:var(--panel); border:1px solid var(--line); border-radius:10px;
  padding:.8rem 1rem; }
.card .num { font-size:1.5rem; font-weight:650; }
.card .label { color:var(--muted); font-size:.82rem; text-transform:uppercase;
  letter-spacing:.06em; }
table { width:100%; border-collapse:collapse; font-size:.9rem; }
th,td { text-align:left; padding:.45rem .55rem; border-bottom:1px solid var(--line);
  vertical-align:top; }
th { color:var(--muted); font-weight:600; font-size:.78rem; text-transform:uppercase;
  letter-spacing:.05em; }
tr:last-child td { border-bottom:none; }
code,pre { font-family:ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace; }
code { background:var(--code); padding:.08rem .3rem; border-radius:4px; }
pre { background:var(--code); border:1px solid var(--line); border-radius:8px;
  padding:.8rem; overflow:auto; font-size:.82rem; }
.badge { display:inline-block; padding:.08rem .45rem; border-radius:999px;
  border:1px solid var(--line); font-size:.75rem; font-weight:600; }
.ok { color:var(--ok); border-color:var(--ok); }
.warn { color:var(--warn); border-color:var(--warn); }
.fail { color:var(--fail); border-color:var(--fail); }
.muted { color:var(--muted); }
.empty { color:var(--muted); font-style:italic; }
ul { margin:.3rem 0 .3rem 1.2rem; padding:0; }
li { margin:.18rem 0; }
footer { margin-top:2rem; padding-top:1rem; border-top:1px solid var(--line);
  color:var(--muted); font-size:.82rem; }
"""


def _e(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _iso(value: object) -> str:
    text = str(value) if value is not None else "-"
    return text.replace("T", " ").replace("+00:00", " UTC")


def _duration(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "-"
    return f"{value * 1000:.0f} ms" if value < 1 else f"{value:.2f} s"


def _size(value: object) -> str:
    if not isinstance(value, int):
        return "-"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _badge(state: str) -> str:
    css = {"ok": "ok", "degraded": "warn", "failed": "fail", "disabled": "muted"}.get(
        state, "muted"
    )
    return f'<span class="badge {css}">{_e(state)}</span>'


def _event_detail(event: dict[str, Any]) -> str:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return ""
    kind = str(event.get("kind", ""))
    if kind.startswith("filesystem."):
        path = _e(payload.get("path", ""))
        suffix = " (directory)" if payload.get("is_dir") else f" ({_size(payload.get('size'))})"
        return f"<code>{path}</code>{suffix}"
    if kind.startswith("process."):
        pid = _e(payload.get("pid", "?"))
        name = _e(payload.get("name", "unknown"))
        command = _e(payload.get("command", ""))
        extra = _e(payload.get("reason", ""))
        lifetime = payload.get("observed_lifetime_s")
        suffix = f" · {_duration(lifetime)}" if isinstance(lifetime, (int, float)) else ""
        reason = f" · {extra}" if extra else ""
        command_html = f" <code>{command}</code>" if command else ""
        return f"pid {pid} · {name}{command_html}{reason}{suffix}"
    if kind == "network.connection":
        process = _e(payload.get("process_name", "unknown"))
        pid = _e(payload.get("pid", "?"))
        local = _e(payload.get("local_address") or "-")
        remote = _e(payload.get("remote_address") or "listening")
        protocol = _e(payload.get("protocol", "TCP"))
        status = _e(payload.get("status", "UNKNOWN"))
        return (
            f"{process} (pid {pid}) · <code>{local}</code> → <code>{remote}</code> "
            f"· {protocol} · {status}"
        )
    return ""


def _event_table(events: list[dict[str, Any]]) -> str:
    if not events:
        return '<p class="empty">No events observed in this category.</p>'
    rows = []
    for event in events:
        rows.append(
            "<tr>"
            f"<td><code>{_e(event.get('kind'))}</code></td>"
            f'<td class="muted">{_iso(event.get("occurred_at"))}</td>'
            f"<td>{_event_detail(event)}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Event</th><th>Observed at</th><th>Details</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def render_html(report: dict[str, Any]) -> str:
    """Render a report dictionary as a standalone HTML document."""

    session = report.get("session", {})
    summary = report.get("summary", {})
    collectors = report.get("collectors", [])
    events = report.get("events", [])
    warnings = report.get("warnings", [])
    limitations = report.get("limitations", [])
    if not isinstance(session, dict):
        session = {}
    if not isinstance(summary, dict):
        summary = {}
    if not isinstance(events, list):
        events = []
    if not isinstance(collectors, list):
        collectors = []

    filesystem = [
        event
        for event in events
        if isinstance(event, dict) and str(event.get("kind", "")).startswith("filesystem.")
    ]
    processes = [
        event
        for event in events
        if isinstance(event, dict) and str(event.get("kind", "")).startswith("process.")
    ]
    network = [
        event
        for event in events
        if isinstance(event, dict) and str(event.get("kind", "")).startswith("network.")
    ]

    collector_rows = []
    for collector in collectors:
        if not isinstance(collector, dict):
            continue
        collector_rows.append(
            "<tr>"
            f"<td>{_e(collector.get('name'))}</td>"
            f"<td>{_badge(str(collector.get('state', 'unknown')))}</td>"
            f"<td>{_e(collector.get('message') or '')}</td>"
            "</tr>"
        )
    collectors_html = (
        "<table><thead><tr><th>Collector</th><th>State</th><th>Message</th></tr></thead>"
        f"<tbody>{''.join(collector_rows)}</tbody></table>"
        if collector_rows
        else '<p class="empty">No collector status recorded.</p>'
    )

    warning_html = "".join(f"<li>{_e(item)}</li>" for item in warnings)
    limitation_html = "".join(f"<li>{_e(item)}</li>" for item in limitations)
    events_total = summary.get("events_total", len(events))
    shown_note = ""
    if report.get("events_shown") is not None and len(events) < int(events_total or 0):
        shown_note = (
            f'<p class="muted">Showing {len(events)} of {_e(events_total)} events. '
            "Use --all to include every event.</p>"
        )

    environment_html = ""
    if "environment_diff" in session:
        environment_html = (
            "<h2>Environment (initial child diff, sanitized)</h2>"
            f"<pre>{_e(json.dumps(session.get('environment_diff'), indent=2, ensure_ascii=False))}</pre>"
        )

    options = session.get("options")
    watch_roots = "-"
    if isinstance(options, dict) and isinstance(options.get("watch_roots"), list):
        watch_roots = ", ".join(str(item) for item in options["watch_roots"])

    title = f"CmdScope report {session.get('ref', '')}".strip()
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<main>
  <h1>CmdScope execution report</h1>
  <p class="sub">Session {_e(session.get("ref"))} · generated {_iso(report.get("generated_at"))}</p>

  <div class="cards">
    <div class="card"><div class="num">{_e(summary.get("filesystem_created"))}</div><div class="label">Files created</div></div>
    <div class="card"><div class="num">{_e(summary.get("filesystem_modified"))}</div><div class="label">Files modified</div></div>
    <div class="card"><div class="num">{_e(summary.get("filesystem_deleted"))}</div><div class="label">Files deleted</div></div>
    <div class="card"><div class="num">{_e(summary.get("processes_started"))}</div><div class="label">Processes started</div></div>
    <div class="card"><div class="num">{_e(summary.get("network_connections"))}</div><div class="label">Network endpoints</div></div>
  </div>

  <h2>Session</h2>
  <div class="panel">
    <table>
      <tr><th>Command</th><td><code>{_e(session.get("command_display"))}</code></td></tr>
      <tr><th>Status</th><td>{_badge(str(session.get("status", "unknown")))}</td></tr>
      <tr><th>Exit status</th><td>{_e(session.get("exit_code") if session.get("exit_code") is not None else "-")}</td></tr>
      <tr><th>Directory</th><td><code>{_e(session.get("cwd"))}</code></td></tr>
      <tr><th>Platform</th><td>{_e(session.get("platform"))}</td></tr>
      <tr><th>Started</th><td>{_iso(session.get("started_at") or session.get("created_at"))}</td></tr>
      <tr><th>Duration</th><td>{_duration(session.get("duration_s"))}</td></tr>
      <tr><th>Watch roots</th><td><code>{_e(watch_roots)}</code></td></tr>
      {"<tr><th>Error</th><td><code>" + _e(session.get("error")) + "</code></td></tr>" if session.get("error") else ""}
    </table>
  </div>

  <h2>Filesystem</h2>
  <div class="panel">{_event_table(filesystem)}</div>

  <h2>Processes</h2>
  <div class="panel">{_event_table(processes)}</div>

  <h2>Network</h2>
  <div class="panel">{_event_table(network)}</div>

  <h2>Collectors</h2>
  <div class="panel">{collectors_html}</div>

  {environment_html}

  <h2>Limitations</h2>
  <div class="panel"><ul>{limitation_html}</ul></div>

  <h2>Warnings</h2>
  <div class="panel"><ul>{warning_html or "<li>None recorded.</li>"}</ul></div>

  {shown_note}
  <footer>Generated locally by CmdScope. No data was transmitted. CmdScope observes; it does not sandbox.</footer>
</main>
</body>
</html>
"""
