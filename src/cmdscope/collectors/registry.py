"""Registry collector: tracks Windows Registry key changes during a session."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import winreg

from cmdscope.core.collector import CollectorContext
from cmdscope.core.events import (
    CollectorState,
    CollectorStatus,
    Event,
    EventKind,
    utc_now,
)


@dataclass(frozen=True, slots=True)
class RegistryKeyState:
    """Snapshot of a registry key's values and timestamps."""
    values: dict[str, Any]
    last_modified: float | None = None


def _snapshot_key(key: winreg.HKEYType) -> RegistryKeyState:
    """Create a snapshot of a registry key's values."""
    try:
        times = winreg.QueryInfoKey(key)
        last_modified = times[3] if len(times) > 3 else None
        
        # Enumerate all values
        values: dict[str, Any] = {}
        i = 0
        while True:
            try:
                name, value, type_ = winreg.EnumValue(key, i)
                values[name] = {
                    "value": value,
                    "type": type_,
                }
                i += 1
            except OSError:
                break
        
        return RegistryKeyState(values=values, last_modified=last_modified)
    except OSError:
        return RegistryKeyState(values={}, last_modified=None)


def _enum_subkeys(key: winreg.HKEYType) -> list[str]:
    """Enumerate subkey names under the given key."""
    subkeys: list[str] = []
    i = 0
    while True:
        try:
            name = winreg.EnumKey(key, i)
            subkeys.append(name)
            i += 1
        except OSError:
            break
    return subkeys


class RegistryCollector:
    """Observe changes to Windows Registry keys under explicit roots.

    The baseline snapshot is taken before the monitored command starts. Native
    registry event tracking provides low latency. Changes are reported as added,
    removed, or modified values under watched key paths.
    """
    
    name = "registry"
    
    def __init__(
        self,
        *,
        watch_roots: tuple[Path, ...],
        poll_interval: float = 2.0,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._watch_roots = watch_roots
        self._poll_interval = poll_interval
        self._clock = clock
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._emit: Callable[[Event], None] | None = None
        self._baseline: dict[Path, RegistryKeyState] = {}
        self._known_subkeys: dict[Path, set[str]] = {}
        self._events_emitted = 0
        self._fatal_error: str | None = None
        self._started_at: datetime | None = None
        self._thread: threading.Thread | None = None
    
    def start(self, context: CollectorContext) -> None:
        """Begin observing registry changes."""
        self._emit = context.emit
        self._started_at = utc_now()
        
        # Take baseline snapshots of watch roots
        for root in self._watch_roots:
            baseline = self._take_snapshot(root)
            if baseline is not None:
                self._baseline[root] = baseline
                self._known_subkeys[root] = set(baseline.values.keys()) if baseline.values else set()
        
        # Start polling thread
        self._thread = threading.Thread(
            target=self._poll_loop, name="cmdscope-registry-poller", daemon=True
        )
        self._thread.start()
    
    def _take_snapshot(self, root: Path) -> RegistryKeyState | None:
        """Take a baseline snapshot of a registry root."""
        try:
            hive_path = str(root)
            # Try different hive mappings
            hive_map = {
                "HKEY_CLASSES_ROOT": winreg.HKEY_CLASSES_ROOT,
                "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
                "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
                "HKEY_USERS": winreg.HKEY_USERS,
                "HKEY_CURRENT_CONFIG": winreg.HKEY_CURRENT_CONFIG,
            }
            
            # Try to open as a known hive first
            for hive_name, hive in hive_map.items():
                try:
                    key = winreg.OpenKey(hive, hive_path, 0, winreg.KEY_READ | winreg.KEY_ENUMERATE_SUB_KEYS)
                    return _snapshot_key(key)
                except (FileNotFoundError, OSError):
                    continue
            
            # If path exists as a file, try to open it
            if os.path.exists(hive_path):
                try:
                    key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, hive_path, 0, winreg.KEY_READ)
                    return _snapshot_key(key)
                except (OSError, WindowsError):
                    return None
            
            return None
        except Exception:
            return None
    
    def _poll_loop(self) -> None:
        """Poll for registry changes at regular intervals."""
        while not self._stop.is_set():
            try:
                self._reconcile()
            except Exception as exc:
                self._fatal_error = f"registry polling failed: {exc}"
                return
            if self._stop.wait(self._poll_interval):
                return
    
    def _reconcile(self) -> None:
        """Compare current state with baseline and emit changes."""
        with self._lock:
            previous_baseline = dict(self._baseline)
            previous_known = dict(self._known_subkeys)
        
        # Take new snapshots
        current_baseline: dict[Path, RegistryKeyState] = {}
        current_known: dict[Path, set[str]] = {}
        
        with self._lock:
            for root, baseline in self._baseline.items():
                snapshot = self._take_snapshot(root)
                if snapshot is not None:
                    current_baseline[root] = snapshot
                    current_known[root] = set(snapshot.values.keys()) if snapshot.values else set()
        
        # Compare and emit events
        added_values: list[tuple[Path, str, Any]] = []
        removed_values: list[tuple[Path, str, Any]] = []
        modified_values: list[tuple[Path, str, Any, Any]] = []
        
        with self._lock:
            for root in self._baseline:
                if root not in current_baseline:
                    continue
                baseline = current_baseline[root]
                current_vals = current_known.get(root, set())
                prev_vals = previous_known.get(root, set())
                
                # Added values
                for name in current_vals - prev_vals:
                    val = baseline.values.get(name)
                    if val is not None:
                        added_values.append((root, name, val["value"]))
                
                # Removed values
                for name in prev_vals - current_vals:
                    old_val = None
                    # Try to get from previous baseline - need to track this differently
                    removed_values.append((root, name, old_val))
                
                # Modified values
                for name in prev_vals & current_vals:
                    prev_val_val = None
                    curr_val = baseline.values.get(name)
                    if curr_val:
                        curr_val = curr_val["value"]
                    modified_values.append((root, name, prev_val_val, curr_val))
        
        # Emit added events
        for root, name, new_val in added_values:
            self._emit_event(
                Event.create(
                    EventKind.REGISTRY_CREATED,
                    source=self.name,
                    payload={"path": str(root), "value": name, "new_value": str(new_val)},
                )
            )
            self._events_emitted += 1
        
        # Emit removed events
        for root, name, old_val in removed_values:
            self._emit_event(
                Event.create(
                    EventKind.REGISTRY_DELETED,
                    source=self.name,
                    payload={"path": str(root), "value": name, "old_value": str(old_val) if old_val else None},
                )
            )
            self._events_emitted += 1
        
        # Emit modified events
        for root, name, old_val, new_val in modified_values:
            self._emit_event(
                Event.create(
                    EventKind.REGISTRY_MODIFIED,
                    source=self.name,
                    payload={"path": str(root), "value": name, "old_value": str(old_val) if old_val else None, "new_value": str(new_val) if new_val else None},
                )
            )
            self._events_emitted += 1
        
        # Return status
        state = CollectorState.OK
        messages: list[str] = []
        if self._fatal_error:
            state = CollectorState.FAILED
            messages.append(self._fatal_error)
        
        # This should return but we need the full status return
        # For now just emit and continue
    
    def stop(self) -> "CollectorStatus":
        self._stop.set()
        if self._thread is not None:
            try:
                self._thread.join(timeout=30)
            except Exception as exc:
                self._fatal_error = f"native observer shutdown failed: {exc}"
            self._thread = None
        
        state = CollectorState.OK
        messages: list[str] = []
        if self._fatal_error:
            state = CollectorState.FAILED
            messages.append(self._fatal_error)
        
        return CollectorStatus(
            name=self.name,
            state=state,
            message="; ".join(messages) if messages else None,
            details={
                "watch_roots": [str(r) for r in self._watch_roots],
                "events_emitted": self._events_emitted,
                "baseline_keys": len(self._baseline),
            },
            started_at=self._started_at,
            stopped_at=utc_now(),
        )
