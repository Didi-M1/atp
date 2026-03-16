"""Utilities for operator-interactive (manual) test steps.

All prompts and status lines are written directly to /dev/tty so they
reach the operator even when pytest captures stdout/stderr.
"""
from __future__ import annotations

import sys
import time

_WIDTH = 72


# ──────────────────────────────────────────────────────────────
# Low-level TTY I/O
# ──────────────────────────────────────────────────────────────

def _tty_write(text: str) -> None:
    """Write text directly to the controlling terminal."""
    try:
        with open("/dev/tty", "w") as tty:
            tty.write(text)
            tty.flush()
    except OSError:
        sys.stderr.write(text)
        sys.stderr.flush()


def _tty_readline() -> str:
    """Read one line directly from the controlling terminal."""
    try:
        with open("/dev/tty") as tty:
            return tty.readline().rstrip("\n")
    except OSError:
        try:
            return input()
        except EOFError:
            return ""


# ──────────────────────────────────────────────────────────────
# Operator prompt
# ──────────────────────────────────────────────────────────────

def prompt_operator(
    *lines: str,
    title: str = "OPERATOR ACTION REQUIRED",
    confirm: str = "Press ENTER when done",
) -> None:
    """Display a prominent bordered instruction box and wait for ENTER.

    Writes directly to /dev/tty so pytest output capture never hides it.

    Args:
        *lines:  Lines of instruction text shown inside the box.
        title:   Short heading at the top of the box.
        confirm: Prompt text appended at the bottom.
    """
    border = "=" * _WIDTH
    sep    = "-" * _WIDTH

    parts = [
        "\n",
        border + "\n",
        f"  *** {title} ***\n",
        sep + "\n",
    ]
    for line in lines:
        parts.append(f"  {line}\n")
    parts.append(border + "\n")
    _tty_write("".join(parts))
    _tty_write(f"  >> {confirm} [ENTER]: ")
    _tty_readline()
    _tty_write("\n")


# ──────────────────────────────────────────────────────────────
# Link state helpers
# ──────────────────────────────────────────────────────────────

def _read_operstate(iface: str) -> str:
    """Return current operstate for *iface* (up / down / unknown / …)."""
    path = f"/sys/class/net/{iface}/operstate"
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return "unknown"


def wait_for_link(
    iface: str,
    target: str,
    timeout_s: int = 15,
    poll_s: float = 0.5,
) -> tuple[bool, str]:
    """Poll operstate until it equals *target* or the timeout expires.

    Prints a live status line (updated in-place via \\r) directly to /dev/tty.

    Args:
        iface:     Network interface name (e.g. ``eth0``).
        target:    Desired state string (``"up"`` or ``"down"``).
        timeout_s: Seconds before giving up.
        poll_s:    Polling interval in seconds.

    Returns:
        ``(success, final_state)`` — success is True when target was reached.
    """
    deadline = time.monotonic() + timeout_s

    while True:
        state = _read_operstate(iface)
        remaining = max(0, int(deadline - time.monotonic()))

        if state == target:
            _tty_write(
                f"\r  {iface}: {state:<10}  [OK — {target.upper()} detected]"
                + " " * 10 + "\n"
            )
            return True, state

        if time.monotonic() >= deadline:
            _tty_write(
                f"\r  {iface}: {state:<10}  [TIMEOUT after {timeout_s}s — "
                f"expected {target.upper()}]" + " " * 5 + "\n"
            )
            return False, state

        _tty_write(
            f"\r  {iface}: {state:<10}  waiting for {target.upper()} "
            f"({remaining}s left)…   "
        )
        time.sleep(poll_s)
