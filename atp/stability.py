"""Reboot / poweroff state machine for multi-iteration stability tests.

State is persisted in JSON files so it survives reboots.
The test runner (pytest) is expected to be re-invoked after each reboot
by an init script (see scripts/atp-reboot-continue.sh).

State files (both in the same directory):
  reboot_state.json  — active reboot sequence (iteration counter, boot times)
  session_state.json — cross-reboot passed-test registry (which tests to skip)

Default location: ~/.atp/
Override via ATP_STATE_FILE environment variable (full path of reboot_state.json).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DEFAULT_STATE_FILE = Path.home() / ".atp" / "reboot_state.json"


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
    """Persist the state dict to disk and fsync so data survives a reboot."""
    sf = _state_file()
    sf.parent.mkdir(parents=True, exist_ok=True)
    with open(sf, "w") as f:
        f.write(json.dumps(state, indent=2))
        f.flush()
        os.fsync(f.fileno())


def clear_state() -> None:
    """Remove the state file (reboot test is complete)."""
    _state_file().unlink(missing_ok=True)


# ── Session state (passed-test registry) ─────────────────────────────────────

def _session_file() -> Path:
    """Path of the session state file (same directory as the reboot state file)."""
    return _state_file().parent / "session_state.json"


def load_passed_tests() -> set[str]:
    """Return the set of test node IDs that passed in an earlier session."""
    try:
        return set(json.loads(_session_file().read_text()).get("passed", []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def record_passed_test(node_id: str) -> None:
    """Append *node_id* to the session state file.

    Uses fsync so the entry survives an imminent sysrq reboot.
    """
    sf = _session_file()
    sf.parent.mkdir(parents=True, exist_ok=True)
    passed = load_passed_tests()
    passed.add(node_id)
    with open(sf, "w") as f:
        f.write(json.dumps({"passed": sorted(passed)}, indent=2))
        f.flush()
        os.fsync(f.fileno())


def clear_session() -> None:
    """Remove the session state file (start a fresh test run)."""
    _session_file().unlink(missing_ok=True)


# ── Uptime ────────────────────────────────────────────────────────────────────

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
