"""Manual (operator-interactive) tests.

These tests require physical intervention from an operator: connecting and
disconnecting cables, inserting removable media, and cycling power.  They
must NOT be run in headless or automated environments.

Profile keys used:
    manual.enabled
    manual.ethernet.link_test[].{name, description, timeout_s}
    manual.ethernet.cable_id[].{name, network_label, description}
    manual.sd_card.{enabled, device, min_size_mb, min_write_mbps, min_read_mbps}
    manual.rtc_battery.{enabled, max_drift_s}

Run order recommendation:
    pytest --profile=<name> -m manual

Skip in CI:
    pytest --profile=<name> -m "not manual"
"""
from __future__ import annotations

import os
import time
from typing import Any

import pytest

from atp.manual import prompt_operator, wait_for_link, _read_operstate
from atp.utils import run_cmd

pytestmark = pytest.mark.manual


# ──────────────────────────────────────────────
# Shared fixture
# ──────────────────────────────────────────────

@pytest.fixture(scope="module")
def manual_cfg(profile: dict[str, Any]) -> dict[str, Any]:
    cfg = profile.get("manual", {})
    if not cfg.get("enabled", False):
        pytest.skip("manual tests disabled in profile (manual.enabled: false)")
    return cfg


# ──────────────────────────────────────────────
# Ethernet — link detect (connect / disconnect)
# ──────────────────────────────────────────────

class TestEthernetLink:
    """Verify that the kernel correctly reports link state changes.

    Reads the list of interfaces to test from ``manual.ethernet.link_test``.
    For each interface the operator is guided through:

        disconnect → verify DOWN → connect → verify UP → disconnect → verify DOWN

    The test fails if the kernel does not report the expected operstate within
    ``timeout_s`` seconds (default 15).

    Profile section::

        manual:
          enabled: true
          ethernet:
            link_test:
              - name: eth0
                description: "Management port"
                timeout_s: 15
              - name: eth1
                description: "Data port"
    """

    @pytest.fixture(autouse=True)
    def _require(self, manual_cfg: dict) -> None:
        pass  # manual_cfg already handles the skip

    def test_ethernet_link_detect(self, manual_cfg: dict[str, Any]) -> None:
        link_ifaces = manual_cfg.get("ethernet", {}).get("link_test", [])
        if not link_ifaces:
            pytest.skip("manual.ethernet.link_test is empty — no interfaces configured")

        prompt_operator(
            "ETHERNET LINK DETECTION TEST",
            "",
            f"This test will check {len(link_ifaces)} interface(s):",
            *[
                f"  • {i['name']}"
                + (f" — {i['description']}" if i.get("description") else "")
                for i in link_ifaces
            ],
            "",
            "You will be guided step-by-step.",
            "Have all Ethernet cables DISCONNECTED before starting.",
            title="MANUAL TEST: Ethernet Link Detect",
            confirm="Press ENTER to begin",
        )

        failures: list[str] = []

        for iface in link_ifaces:
            name  = iface["name"]
            desc  = iface.get("description", name)
            tmo   = iface.get("timeout_s", 15)
            label = f"{name} ({desc})"

            # ── Step 1: confirm cable is disconnected ─────────────────────
            prompt_operator(
                f"[{name}]  Step 1 of 3: Ensure cable is DISCONNECTED",
                "",
                f"  Interface : {label}",
                "",
                "If a cable is connected to this port, please REMOVE it now.",
                title="DISCONNECT cable",
                confirm="Cable is disconnected — press ENTER",
            )

            ok, state = wait_for_link(name, "down", timeout_s=tmo)
            if not ok:
                failures.append(
                    f"{name}: link is still '{state}' after disconnect "
                    "(expected DOWN) — check cable is fully removed"
                )
                continue

            # ── Step 2: connect the cable ─────────────────────────────────
            prompt_operator(
                f"[{name}]  Step 2 of 3: CONNECT cable",
                "",
                f"  Interface : {label}",
                "",
                "Plug an Ethernet cable into this port now.",
                "The link LED should light up within a few seconds.",
                title="CONNECT cable",
                confirm="Cable is connected — press ENTER",
            )

            ok, state = wait_for_link(name, "up", timeout_s=tmo)
            if not ok:
                failures.append(
                    f"{name}: link did not come UP after connect "
                    f"(state='{state}', timeout={tmo}s) — "
                    "check cable, switch port, and SFP if applicable"
                )
                continue  # skip step 3 — no point asking to disconnect if it never came up

            # ── Step 3: disconnect and confirm DOWN ───────────────────────
            prompt_operator(
                f"[{name}]  Step 3 of 3: DISCONNECT cable",
                "",
                f"  Interface : {label}",
                "",
                "Remove the Ethernet cable from this port.",
                "The link LED should turn off.",
                title="DISCONNECT cable",
                confirm="Cable is disconnected — press ENTER",
            )

            ok, state = wait_for_link(name, "down", timeout_s=tmo)
            if not ok:
                failures.append(
                    f"{name}: link did not go DOWN after disconnect "
                    f"(state='{state}', timeout={tmo}s) — "
                    "check that the cable is fully removed"
                )

        # ── Reconnect all tested interfaces so subsequent network tests work ─
        prompt_operator(
            "All link-detect tests finished.",
            "",
            "Please RECONNECT cables to the following interfaces so that",
            "subsequent network tests can run:",
            *[
                f"  • {i['name']}"
                + (f" — {i['description']}" if i.get("description") else "")
                for i in link_ifaces
            ],
            title="RECONNECT cables",
            confirm="All cables reconnected — press ENTER",
        )

        if failures:
            pytest.fail(
                "Ethernet link detection failures:\n"
                + "\n".join(f"  ✗ {f}" for f in failures)
            )


# ──────────────────────────────────────────────
# Ethernet — cable / network assignment
# ──────────────────────────────────────────────

class TestEthernetCableID:
    """Verify that each physical port is wired to the expected network.

    Reads the list of interfaces to verify from ``manual.ethernet.cable_id``.
    For each interface the operator connects the cable from the named network
    to the port they believe is correct.  The test then verifies that:

    * The link appeared on the *expected* OS interface.
    * No *other* cable-ID interface unexpectedly went UP.

    This catches cabling mistakes, PCB layout errors, and kernel driver
    naming inconsistencies.

    Profile section::

        manual:
          enabled: true
          ethernet:
            cable_id:
              - name: eth0
                network_label: "MANAGEMENT"
                description: "Management port"
              - name: eth1
                network_label: "DATA"
                description: "Data port"
    """

    @pytest.fixture(autouse=True)
    def _require(self, manual_cfg: dict) -> None:
        pass

    def test_ethernet_cable_id(self, manual_cfg: dict[str, Any]) -> None:
        cable_ifaces = manual_cfg.get("ethernet", {}).get("cable_id", [])
        if not cable_ifaces:
            pytest.skip("manual.ethernet.cable_id is empty — no interfaces configured")

        tmo = 15

        prompt_operator(
            "ETHERNET CABLE ASSIGNMENT TEST",
            "",
            "This test verifies that each physical port is wired to",
            "the correct network.  You will be asked to connect one",
            "cable at a time.",
            "",
            f"Interfaces under test ({len(cable_ifaces)}):",
            *[
                f"  • {i['name']} → expected label: {i['network_label']}"
                + (f" ({i['description']})" if i.get("description") else "")
                for i in cable_ifaces
            ],
            "",
            "Start with ALL cables DISCONNECTED from these ports.",
            title="MANUAL TEST: Ethernet Cable Assignment",
            confirm="All cables disconnected — press ENTER to begin",
        )

        failures: list[str] = []

        for iface in cable_ifaces:
            name  = iface["name"]
            label = iface["network_label"]
            desc  = iface.get("description", name)

            # Ensure all interfaces are down before this round
            still_up = [
                i["name"] for i in cable_ifaces
                if _read_operstate(i["name"]) == "up"
            ]
            if still_up:
                prompt_operator(
                    f"Some interfaces still show link UP: {', '.join(still_up)}",
                    "Please disconnect ALL cables before continuing.",
                    title="WAIT — cables still connected",
                    confirm="All disconnected — press ENTER",
                )

            prompt_operator(
                f"Connect the  '{label}'  network cable",
                "",
                f"  Expected interface : {name} ({desc})",
                "",
                "Plug the cable from the network/switch labeled",
                f"  '{label}'",
                "into what you believe is the correct port on the platform.",
                title=f"CONNECT — {label} cable",
                confirm="Cable connected — press ENTER",
            )

            ok, final_state = wait_for_link(name, "up", timeout_s=tmo)

            # Also check that no OTHER cable-ID interface unexpectedly went up
            unexpected_up = [
                i["name"]
                for i in cable_ifaces
                if i["name"] != name and _read_operstate(i["name"]) == "up"
            ]

            if not ok:
                failures.append(
                    f"{label}: expected link UP on '{name}' but state is "
                    f"'{final_state}' after {tmo}s — "
                    "wrong port or cable not connected?"
                )
            elif unexpected_up:
                failures.append(
                    f"{label}: link appeared on UNEXPECTED interface(s) "
                    f"{unexpected_up} instead of (or in addition to) '{name}' — "
                    "possible cabling error or wrong interface mapping"
                )

            # Disconnect before testing the next network
            prompt_operator(
                f"DISCONNECT the '{label}' cable",
                "",
                "Remove it before testing the next network.",
                title=f"DISCONNECT — {label} cable",
                confirm="Cable removed — press ENTER",
            )

        if failures:
            pytest.fail(
                "Ethernet cable assignment failures:\n"
                + "\n".join(f"  ✗ {f}" for f in failures)
            )


# ──────────────────────────────────────────────
# SD card — insert / write-read / remove
# ──────────────────────────────────────────────

class TestSDCard:
    """Validate SD card insertion detection and basic read/write throughput.

    Test sequence:
    1. Operator inserts SD card → system detects block device.
    2. Automated read/write test (dd, no mount required).
    3. Operator removes SD card → block device disappears.

    Profile section::

        manual:
          sd_card:
            enabled: true
            device:         /dev/mmcblk0
            min_size_mb:    null
            min_write_mbps: null
            min_read_mbps:  null
    """

    @pytest.fixture(autouse=True)
    def _require(self, manual_cfg: dict) -> None:
        if not manual_cfg.get("sd_card", {}).get("enabled", False):
            pytest.skip("manual.sd_card not enabled in profile")

    def _device_present(self, device: str) -> bool:
        return os.path.exists(device)

    def _wait_for_device(self, device: str, present: bool, timeout_s: int = 15) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._device_present(device) == present:
                return True
            time.sleep(0.5)
        return False

    def test_sd_insert_detect(self, manual_cfg: dict) -> None:
        device = manual_cfg["sd_card"].get("device")
        if not device:
            pytest.skip("manual.sd_card.device not set in profile")

        prompt_operator(
            "SD CARD INSERT TEST",
            "",
            f"  Block device : {device}",
            "",
            "INSERT the SD card into the slot now.",
            title="INSERT SD card",
            confirm="SD card inserted — press ENTER",
        )

        assert self._wait_for_device(device, present=True, timeout_s=15), (
            f"SD card block device '{device}' did not appear after 15 s — "
            "check card is fully seated and the slot/driver is functional"
        )

    def test_sd_write_read(self, manual_cfg: dict) -> None:
        sd     = manual_cfg["sd_card"]
        device = sd.get("device")
        if not device:
            pytest.skip("manual.sd_card.device not set in profile")
        if not self._device_present(device):
            pytest.skip(f"SD card device '{device}' not present — run test_sd_insert_detect first")

        errors: list[str] = []

        min_size_mb    = sd.get("min_size_mb")
        min_write_mbps = sd.get("min_write_mbps")
        min_read_mbps  = sd.get("min_read_mbps")

        if min_size_mb is not None:
            rc, out, _ = run_cmd(f"blockdev --getsize64 {device}")
            if rc == 0:
                actual_mb = int(out.strip()) / (1024 * 1024)
                if actual_mb < min_size_mb:
                    errors.append(f"SD card size {actual_mb:.0f} MiB < minimum {min_size_mb} MiB")
            else:
                errors.append("Could not read SD card size: blockdev failed")

        test_mb = min(64, min_size_mb or 64)

        _, out, err = run_cmd(
            f"dd if=/dev/zero of={device} bs=1M count={test_mb} oflag=direct 2>&1",
            timeout=120,
        )
        write_mbps = _parse_dd_mbps(out + err)
        if write_mbps is not None and min_write_mbps is not None and write_mbps < min_write_mbps:
            errors.append(f"SD write {write_mbps:.1f} MB/s < minimum {min_write_mbps} MB/s")

        _, out, err = run_cmd(
            f"dd if={device} of=/dev/null bs=1M count={test_mb} iflag=direct 2>&1",
            timeout=120,
        )
        read_mbps = _parse_dd_mbps(out + err)
        if read_mbps is not None and min_read_mbps is not None and read_mbps < min_read_mbps:
            errors.append(f"SD read {read_mbps:.1f} MB/s < minimum {min_read_mbps} MB/s")

        if errors:
            pytest.fail("SD card read/write failures:\n" + "\n".join(f"  ✗ {e}" for e in errors))

    def test_sd_remove_detect(self, manual_cfg: dict) -> None:
        device = manual_cfg["sd_card"].get("device")
        if not device:
            pytest.skip("manual.sd_card.device not set in profile")

        prompt_operator(
            "SD CARD REMOVE TEST",
            "",
            f"  Block device : {device}",
            "",
            "REMOVE the SD card from the slot now.",
            title="REMOVE SD card",
            confirm="SD card removed — press ENTER",
        )

        assert self._wait_for_device(device, present=False, timeout_s=15), (
            f"SD card block device '{device}' is still visible after 15 s — "
            "check that the card was fully ejected"
        )


# ──────────────────────────────────────────────
# RTC battery — power-cycle preservation
# ──────────────────────────────────────────────

class TestRTCBattery:
    """Verify that the RTC retains the correct time across a full power cycle.

    Profile section::

        manual:
          rtc_battery:
            enabled: true
            max_drift_s: 5
    """

    @pytest.fixture(autouse=True)
    def _require(self, manual_cfg: dict) -> None:
        if not manual_cfg.get("rtc_battery", {}).get("enabled", False):
            pytest.skip("manual.rtc_battery not enabled in profile")

    def _hwclock_epoch(self) -> float | None:
        rc, out, _ = run_cmd("hwclock --show --utc")
        if rc != 0 or not out.strip():
            return None
        rc2, out2, _ = run_cmd(
            f'date -u -d "{out.strip()}" +%s 2>/dev/null || '
            f'date -u -jf "%Y-%m-%d %H:%M:%S" "{out.strip()[:19]}" +%s'
        )
        try:
            return float(out2.strip())
        except (ValueError, AttributeError):
            return None

    def test_rtc_battery_power_cycle(self, manual_cfg: dict) -> None:
        max_drift_s = manual_cfg.get("rtc_battery", {}).get("max_drift_s", 5)

        before_ts = self._hwclock_epoch()
        if before_ts is None:
            pytest.skip("hwclock --show failed — check RTC device and hwclock tool")

        wall_before = time.time()

        prompt_operator(
            "RTC BATTERY POWER-CYCLE TEST",
            "",
            "STEP 1: DISCONNECT mains power from the platform.",
            "STEP 2: Wait at least 10 seconds.",
            "STEP 3: RECONNECT mains power and wait for the system to boot.",
            "",
            "NOTE: atp-reboot-continue.sh must be wired into the init system",
            "      so pytest resumes automatically after boot.",
            title="POWER-CYCLE for RTC battery test",
            confirm="Press ENTER after power is restored and system has booted",
        )

        wall_elapsed = time.time() - wall_before
        after_ts     = self._hwclock_epoch()
        if after_ts is None:
            pytest.fail("hwclock --show failed after power cycle — RTC may have lost its settings")

        drift = abs((after_ts - before_ts) - wall_elapsed)
        assert drift <= max_drift_s, (
            f"RTC battery drift {drift:.1f}s exceeds limit of {max_drift_s}s "
            f"(RTC advanced {after_ts - before_ts:.1f}s, wall clock advanced {wall_elapsed:.1f}s)"
        )


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _parse_dd_mbps(output: str) -> float | None:
    import re
    m = re.search(r"([\d.]+)\s*(G|M|k)?B/s", output, re.IGNORECASE)
    if not m:
        return None
    value = float(m.group(1))
    unit  = (m.group(2) or "M").upper()
    if unit == "G":
        return value * 1024
    if unit == "K":
        return value / 1024
    return value
