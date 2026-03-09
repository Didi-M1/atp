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

import time
from pathlib import Path

import pytest

from atp import stability
from atp.utils import run_cmd

pytestmark = pytest.mark.stability

_SYSRQ_REBOOT = "/proc/sysrq-trigger"


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
        assert Path(_SYSRQ_REBOOT).exists(), (
            f"{_SYSRQ_REBOOT} not found — enable CONFIG_MAGIC_SYSRQ in kernel "
            "or set kernel.sysrq=1"
        )

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
        assert Path(_SYSRQ_REBOOT).exists(), (
            f"{_SYSRQ_REBOOT} not found — sysrq must be enabled"
        )

        print("\nIssuing poweroff via sysrq-o — power on manually after shutdown.")
        Path(_SYSRQ_REBOOT).write_text("o")
        time.sleep(30)
        pytest.fail("Poweroff was triggered but system did not power off within 30 s")
