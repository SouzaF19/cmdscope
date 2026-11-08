"""Network collector tests."""

from __future__ import annotations

import socket
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from cmdscope.collectors.network import NetworkCollector, _endpoint, _family_name
from cmdscope.core.events import CollectorState, EventKind
from cmdscope.core.registry import ProcessRegistry
from tests.support import make_context


def _connection(pid: int = 4242) -> SimpleNamespace:
    return SimpleNamespace(
        pid=pid,
        family=socket.AF_INET,
        type=socket.SOCK_STREAM,
        laddr=("127.0.0.1", 5000),
        raddr=("93.184.216.34", 443),
        status="ESTABLISHED",
    )


def test_endpoint_formatting() -> None:
    assert _endpoint(("127.0.0.1", 80)) == "127.0.0.1:80"
    assert _endpoint(("::1", 80)) == "[::1]:80"
    assert _endpoint(None) is None
    assert _family_name(socket.AF_INET) == "IPv4"
    assert _family_name(socket.AF_INET6) == "IPv6"


def test_network_collector_attributes_known_pids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "cmdscope.collectors.network.psutil.net_connections",
        lambda kind="inet": [_connection()],
    )
    events: list = []
    registry = ProcessRegistry()
    registry.add(4242, "curl")
    collector = NetworkCollector(poll_interval=0.02)
    collector.start(make_context(tmp_path, events.append, registry=registry))
    collector.stop()

    network_events = [event for event in events if event.kind == EventKind.NETWORK_CONNECTION]
    assert len(network_events) == 1
    payload = network_events[0].payload
    assert payload["pid"] == 4242
    assert payload["process_name"] == "curl"
    assert payload["remote_address"] == "93.184.216.34:443"
    assert payload["protocol"] == "TCP"


def test_network_collector_ignores_unknown_pids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "cmdscope.collectors.network.psutil.net_connections",
        lambda kind="inet": [_connection(pid=9999)],
    )
    events: list = []
    registry = ProcessRegistry()
    registry.add(4242, "curl")
    collector = NetworkCollector(poll_interval=0.02)
    collector.start(make_context(tmp_path, events.append, registry=registry))
    collector.stop()
    assert not [event for event in events if event.kind == EventKind.NETWORK_CONNECTION]


def test_network_collector_reports_unattributed_sockets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    anonymous = SimpleNamespace(
        pid=None,
        family=socket.AF_INET,
        type=socket.SOCK_STREAM,
        laddr=("127.0.0.1", 5000),
        raddr=(),
        status="NONE",
    )
    monkeypatch.setattr(
        "cmdscope.collectors.network.psutil.net_connections",
        lambda kind="inet": [anonymous],
    )
    events: list = []
    registry = ProcessRegistry()
    registry.add(4242, "curl")
    collector = NetworkCollector(poll_interval=0.02)
    collector.start(make_context(tmp_path, events.append, registry=registry))
    status = collector.stop()
    assert status.state == CollectorState.DEGRADED
    assert status.message is not None
    assert status.details["unattributed_sockets"] == 1
    assert not [event for event in events if event.kind == EventKind.NETWORK_CONNECTION]


def test_network_collector_reports_access_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def deny(kind="inet"):
        raise psutil.AccessDenied(1234, "access denied")

    monkeypatch.setattr("cmdscope.collectors.network.psutil.net_connections", deny)
    collector = NetworkCollector(poll_interval=0.02)
    collector.start(make_context(tmp_path, lambda event: None))
    status = collector.stop()
    assert status.state == CollectorState.DEGRADED
    assert status.message is not None
    assert status.details["access_denied"] is True
