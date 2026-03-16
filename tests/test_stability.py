"""Stability tests: reboot sequence and poweroff.

Reboot test flow
----------------
1. First run (no state file):
   - Write ~/.atp/reboot_state.json with {iteration: 0, total: N, ...}
   - Trigger reboot via sysrq
   - pytest process exits (reboot)

2. After reboot, init system calls scripts/atp-reboot-continue.sh which
   re-invokes: pytest --profile=<name> tests/test_stability.py

3. Continuation run (state file exists, iteration < total):
   - Record uptime (boot time)
   - Increment iteration
   - If more reboots remain: reboot again
   - If this was the last reboot: clear state file first, then assert boot
     times (file is removed whether the assertions pass or fail, so the
     system is never stuck in continuation mode after the final iteration)

Profile keys used:
    stability.reboot.enabled, .count, .max_boot_time_s
    stability.poweroff.enabled
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from atp import stability
from atp.utils import run_cmd

pytestmark = pytest.mark.stability

_SYSRQ_REBOOT   = "/proc/sysrq-trigger"
_SYSRQ_CTRL     = "/proc/sys/kernel/sysrq"
_SYSRQ_BIT_REBOOT = 128   # bit 7 — allow reboot/poweroff via sysrq


def _check_sysrq_reboot() -> None:
    """Verify the current user can trigger a reboot via sysrq.

    Checks three things in order and fails with a clear explanation if any
    condition is not met:

    1. /proc/sysrq-trigger exists  → kernel built without CONFIG_MAGIC_SYSRQ
    2. /proc/sysrq-trigger is writable → process lacks write permission
    3. /proc/sys/kernel/sysrq has the reboot bit set → sysrq disabled at runtime
    """
    if os.getuid() != 0:
        pytest.fail(
            "Reboot test requires root privileges "
            f"(current uid={os.getuid()}).\n"
            "Re-run the test as root:\n"
            "  sudo pytest --profile=<name> tests/test_stability.py"
        )

    trigger = Path(_SYSRQ_REBOOT)

    if not trigger.exists():
        pytest.fail(
            f"{_SYSRQ_REBOOT} not found.\n"
            "The kernel must be built with CONFIG_MAGIC_SYSRQ=y.\n"
            "For the ATP QEMU image this is already enabled; on a custom kernel\n"
            "rebuild with that option or add 'sysrq_always_enabled=1' to the\n"
            "kernel command line."
        )

    if not os.access(_SYSRQ_REBOOT, os.W_OK):
        pytest.fail(
            f"{_SYSRQ_REBOOT} exists but is not writable by the current user "
            f"(uid={os.getuid()}).\n"
            "Run the test as root, or grant write access with:\n"
            "  chmod a+w /proc/sysrq-trigger\n"
            "or add the running user to a group that can write to it."
        )

    sysrq_val = 0
    try:
        sysrq_val = int(Path(_SYSRQ_CTRL).read_text().strip())
    except (OSError, ValueError):
        pass  # if we can't read it, assume it's enabled and let the write fail

    if sysrq_val == 0:
        pytest.fail(
            f"sysrq is disabled ('{_SYSRQ_CTRL}' == 0).\n"
            "Enable it at runtime with:\n"
            "  echo 1 > /proc/sys/kernel/sysrq\n"
            "or permanently via /etc/sysctl.conf:\n"
            "  kernel.sysrq = 1\n"
            "For the ATP QEMU image add 'sysrq_always_enabled=1' to the\n"
            "kernel command line (already set in qemu-run.sh)."
        )

    reboot_allowed = (sysrq_val == 1) or bool(sysrq_val & _SYSRQ_BIT_REBOOT)
    if not reboot_allowed:
        pytest.fail(
            f"sysrq is enabled ('{_SYSRQ_CTRL}' == {sysrq_val}) but the reboot\n"
            f"bit (bit 7 = {_SYSRQ_BIT_REBOOT}) is not set.\n"
            "Allow reboot via sysrq with:\n"
            f"  echo $(( {sysrq_val} | {_SYSRQ_BIT_REBOOT} )) > {_SYSRQ_CTRL}\n"
            "or enable all sysrq functions:\n"
            f"  echo 1 > {_SYSRQ_CTRL}"
        )


def _do_reboot() -> None:
    """Trigger an immediate reboot via sysrq."""
    Path(_SYSRQ_REBOOT).write_text("b")
    # If we get here the write worked but reboot hasn't fired yet — wait.
    time.sleep(10)
    pytest.fail("Reboot was triggered but system did not reboot within 10 s")


# ──────────────────────────────────────────────
# Reboot sequence
# ──────────────────────────────────────────────

class TestReboot:

    @pytest.fixture(autouse=True)
    def _require(self, profile):
        if not profile.get("stability", {}).get("reboot", {}).get("enabled", False):
            pytest.skip("reboot tests disabled in profile")

    def test_reboot_sequence(self, profile, profile_name, profiles_dir):
        """Perform N reboots and verify each boot completes within max_boot_time_s."""
        reboot_cfg = profile["stability"]["reboot"]
        total: int = reboot_cfg["count"]
        max_boot_s: float | None = reboot_cfg.get("max_boot_time_s")

        # ── Continuation path ──────────────────────────────────────────────
        if stability.is_continuation():
            state = stability.get_state()
            iteration: int = state["iteration"]
            boot_times: list[float] = state["boot_times_s"]

            # Record how long this boot took
            uptime = stability.current_uptime_s()
            boot_times.append(uptime)
            iteration += 1

            if iteration < total:
                # More reboots to do
                state["iteration"] = iteration
                state["boot_times_s"] = boot_times
                stability.save_state(state)
                _do_reboot()  # noreturn
            else:
                # Final iteration — remove the state file now so that a
                # subsequent assertion failure does not leave the system
                # stuck in continuation mode on the next run.
                stability.clear_state()
                summary = ", ".join(f"{t:.1f}s" for t in boot_times)
                if max_boot_s is not None:
                    assert uptime <= max_boot_s, (
                        f"Reboot {iteration}/{total}: boot took {uptime:.1f} s "
                        f"(limit {max_boot_s} s)"
                    )
                print(
                    f"\nReboot test PASSED — {total} reboots completed.\n"
                    f"Boot times: {summary}"
                )
                return  # test passes

        # ── First run ──────────────────────────────────────────────────────
        _check_sysrq_reboot()

        state = stability.new_state(
            profile_name=profile_name,
            total=total,
            profiles_dir=profiles_dir,
        )
        stability.save_state(state)

        print(
            f"\nStarting reboot sequence: {total} reboots planned.\n"
            f"Re-invoke pytest via scripts/atp-reboot-continue.sh after each boot."
        )
        _do_reboot()  # noreturn


# ──────────────────────────────────────────────
# Poweroff
# ──────────────────────────────────────────────

class TestPoweroff:

    @pytest.fixture(autouse=True)
    def _require(self, profile):
        if not profile.get("stability", {}).get("poweroff", {}).get("enabled", False):
            pytest.skip("poweroff tests disabled in profile")

    def test_poweroff(self, profile):
        """Issue a poweroff command.

        After this test the operator must:
        1. Physically power the board back on.
        2. Verify the system boots correctly (manually or via another test run).
        """
        _check_sysrq_reboot()

        print("\nIssuing poweroff via sysrq-o — power on manually after shutdown.")
        Path(_SYSRQ_REBOOT).write_text("o")
        time.sleep(30)
        pytest.fail("Poweroff was triggered but system did not power off within 30 s")
