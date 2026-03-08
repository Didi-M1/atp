"""Reboot / poweroff state machine for multi-iteration stability tests.

State is persisted in a JSON file so it survives reboots.
The test runner (pytest) is expected to be re-invoked after each reboot
by an init script (see scripts/atp-reboot-continue.sh).

State file location: /var/lib/atp/reboot_state.json
(Override via ATP_STATE_FILE environment variable for testing.)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DEFAULT_STATE_FILE = Path("/var/lib/atp/reboot_state.json")


def _state_file() -> Path:
    custom = os.environ.get("ATP_STATE_FILE")
    return Path(custom) if custom else _DEFAULT_STATE_FILE


def is_continuation() -> bool:
    """Return True if a reboot test is already in progress."""
    return _state_file().exists()


def get_state() -> dict[str, Any]:
    """Return the current state dict, or {} if none exists."""
    sf = _state_file()
    if sf.exists():
        return json.loads(sf.read_text())
    return {}


def save_state(state: dict[str, Any]) -> None:
    """Persist the state dict to disk."""
    sf = _state_file()
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text(json.dumps(state, indent=2))


def clear_state() -> None:
    """Remove the state file (reboot test is complete)."""
    _state_file().unlink(missing_ok=True)


def current_uptime_s() -> float:
    """Return system uptime in seconds (from /proc/uptime)."""
    raw = Path("/proc/uptime").read_text().split()[0]
    return float(raw)


def new_state(profile_name: str, total: int, profiles_dir: str | None = None) -> dict[str, Any]:
    """Build an initial state dict for a fresh reboot sequence."""
    return {
        "profile": profile_name,
        "profiles_dir": profiles_dir,
        "iteration": 0,
        "total": total,
        "boot_times_s": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
