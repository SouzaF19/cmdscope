"""Network endpoint collector based on psutil polling and the process registry."""

from __future__ import annotations

import socket
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any

import psutil

from cmdscope.core.collector import CollectorContext
from cmdscope.core.events import (
    CollectorState,
    CollectorStatus,
    Event,
    EventKind,
    utc_now,
)
from cmdscope.core.registry import ProcessRegistry


def _endpoint(value: Any) -> str | None:
    if not value:
        return None
    try:
        host, port = value[0], value[1]
    except (TypeError, IndexError):
        return str(value)
    host_text = str(host)
    if ":" in host_text and not host_text.startswith("["):
        host_text = f"[{host_text}]"
    return f"{host_text}:{port}"


def _family_name(family: Any) -> str:
    if family == socket.AF_INET6:
        return "IPv6"
    if family == socket.AF_INET:
        return "IPv4"
    return str(family)


def _protocol_name(protocol: Any) -> str:
    if protocol == socket.SOCK_STREAM:
        return "TCP"
    if protocol == socket.SOCK_DGRAM:
        return "UDP"
    return str(protocol)


class NetworkCollector:
    """Attribute observed inet sockets to processes already in the session.

    Sockets are sampled. Connections shorter than one poll interval can be
    missed. No packet contents or DNS lookups are used.
    """

    name = "network"

    def __init__(self, *, poll_interval: float) -> None:
        self._poll_interval = poll_interval
        self._stop = threading.Event()
        self._registry = ProcessRegistry()
        self._emit: Callable[[Event], None] | None = None
        self._thread: threading.Thread | None = None
        self._seen: set[tuple[Any, ...]] = set()
        self._access_denied = False
        self._error: str | None = None
        self._started_at: datetime | None = None
        self._connections = 0

    def start(self, context: CollectorContext) -> None:
        self._registry = context.registry
        self._emit = context.emit
        self._started_at = utc_now()
        self._thread = threading.Thread(
            target=self._poll_loop, name="cmdscope-network-poller", daemon=True
        )
        self._thread.start()

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:  # pragma: no cover - defensive thread boundary
                self._error = f"network polling failed: {exc}"
                return
            if self._stop.wait(self._poll_interval):
                return

    def poll_once(self) -> None:
        """Sample sockets once. Public for deterministic diagnostics/tests."""

        try:
            connections = psutil.net_connections(kind="inet")
        except (psutil.AccessDenied, PermissionError) as exc:
            self._access_denied = True
            self._error = f"network inspection access denied: {exc}"
            return
        except OSError as exc:
            self._error = f"network inspection failed: {exc}"
            return

        registry = self._registry.snapshot()
        for connection in connections:
            pid = getattr(connection, "pid", None)
            if pid is None or pid not in registry:
                continue
            local = getattr(connection, "laddr", None)
            remote = getattr(connection, "raddr", None)
            family = getattr(connection, "family", None)
            protocol = getattr(connection, "type", None)
            key = (
                pid,
                family,
                protocol,
                tuple(local) if local else None,
                tuple(remote) if remote else None,
            )
            if key in self._seen:
                continue
            self._seen.add(key)
            payload = {
                "pid": pid,
                "process_name": registry[pid],
                "family": _family_name(family),
                "protocol": _protocol_name(protocol),
                "local_address": _endpoint(local),
                "remote_address": _endpoint(remote),
                "status": str(getattr(connection, "status", "") or "UNKNOWN"),
            }
            self._emit_event(
                Event.create(EventKind.NETWORK_CONNECTION, source=self.name, payload=payload)
            )

    def _emit_event(self, event: Event) -> None:
        if self._emit is None:
            return
        try:
            self._emit(event)
            self._connections += 1
        except Exception as exc:  # pragma: no cover - defensive queue boundary
            self._error = f"event emission failed: {exc}"
            self._stop.set()

    def stop(self) -> CollectorStatus:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=30)
            self._thread = None
        try:
            self.poll_once()
        except Exception as exc:
            self._error = f"final network poll failed: {exc}"

        state = CollectorState.OK
        messages: list[str] = []
        if self._error and self._access_denied:
            state = CollectorState.DEGRADED
            messages.append(self._error)
        elif self._error:
            state = CollectorState.FAILED
            messages.append(self._error)

        return CollectorStatus(
            name=self.name,
            state=state,
            message="; ".join(messages) if messages else None,
            details={
                "connections_observed": self._connections,
                "access_denied": self._access_denied,
                "poll_interval": self._poll_interval,
            },
            started_at=self._started_at,
            stopped_at=utc_now(),
        )
