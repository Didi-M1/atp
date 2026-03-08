"""Firmware / BIOS / UEFI tests and arbitrary custom shell checks.

Profile keys used:
    firmware.enabled
    firmware.dmi.enabled, firmware.dmi.checks  (sysfs names → expected values)
    firmware.uefi.enabled, firmware.uefi.boot_order_first, firmware.uefi.power_restore
    custom_checks[].{name, command, expected_returncode, expected_output, description}

DMI sysfs field names (under /sys/class/dmi/id/):
    bios_vendor, bios_version, bios_date, bios_release
    sys_vendor, product_name, product_version, product_serial
    board_vendor, board_name, board_version
"""
from __future__ import annotations

import os

import pytest

from atp.utils import run_cmd, read_file, tool_exists

pytestmark = pytest.mark.firmware


# ──────────────────────────────────────────────
# DMI (via /sys/class/dmi/id/ — no dmidecode needed)
# ──────────────────────────────────────────────

class TestDMI:

    @pytest.fixture(autouse=True)
    def _require(self, profile):
        fw = profile.get("firmware", {})
        if not fw.get("enabled", False):
            pytest.skip("firmware tests disabled in profile")
        if not fw.get("dmi", {}).get("enabled", False):
            pytest.skip("DMI tests disabled in profile")

    def test_dmi_sysfs_available(self):
        assert os.path.isdir("/sys/class/dmi/id"), (
            "/sys/class/dmi/id not found — DMI may not be supported on this platform"
        )

    def test_dmi_checks(self, profile):
        checks = profile.get("firmware", {}).get("dmi", {}).get("checks", {})
        if not checks:
            pytest.skip("No DMI checks defined in profile")

        failures = []
        for field, expected in checks.items():
            path = f"/sys/class/dmi/id/{field}"
            actual = read_file(path)
            if actual is None:
                failures.append(f"  {field}: cannot read {path}")
            elif actual != str(expected):
                failures.append(
                    f"  {field}: expected '{expected}', got '{actual}'"
                )

        if failures:
            pytest.fail("DMI field mismatches:\n" + "\n".join(failures))


# ──────────────────────────────────────────────
# UEFI (via efibootmgr)
# ──────────────────────────────────────────────

# Mapping from profile keywords to substrings found in efibootmgr output
_BOOT_ORDER_KEYWORDS = {
    "hdd":  ("HD(", "SATA", "Hard"),
    "usb":  ("USB",),
    "pxe":  ("PXE", "IPv4", "IPv6", "Network"),
    "net":  ("PXE", "IPv4", "IPv6", "Network"),
}


class TestUEFI:

    @pytest.fixture(autouse=True)
    def _require(self, profile):
        fw = profile.get("firmware", {})
        if not fw.get("enabled", False):
            pytest.skip("firmware tests disabled in profile")
        if not fw.get("uefi", {}).get("enabled", False):
            pytest.skip("UEFI tests disabled in profile")
        if not os.path.isdir("/sys/firmware/efi"):
            pytest.skip("System is not booted in UEFI mode")

    def test_efibootmgr_available(self):
        if not tool_exists("efibootmgr"):
            pytest.skip("efibootmgr not installed — add to packages.txt")

    def test_boot_order_first_entry(self, profile):
        expected_type = (
            profile.get("firmware", {}).get("uefi", {}).get("boot_order_first")
        )
        if not expected_type:
            pytest.skip("boot_order_first not set in profile")

        if not tool_exists("efibootmgr"):
            pytest.skip("efibootmgr not available")

        rc, out, _ = run_cmd("efibootmgr -v")
        assert rc == 0, "efibootmgr failed"

        # Find "BootOrder:" line, take the first entry number
        boot_order_line = ""
        boot_entries: dict[str, str] = {}
        for line in out.splitlines():
            if line.startswith("BootOrder:"):
                boot_order_line = line
            elif line.startswith("Boot") and "\t" in line:
                num = line[4:8]
                boot_entries[num] = line

        if not boot_order_line:
            pytest.fail("Could not find BootOrder in efibootmgr output")

        order = boot_order_line.split(":", 1)[1].strip().split(",")
        first_num = order[0].strip()
        first_entry = boot_entries.get(first_num, "")

        keywords = _BOOT_ORDER_KEYWORDS.get(expected_type.lower(), (expected_type,))
        found = any(kw.upper() in first_entry.upper() for kw in keywords)
        assert found, (
            f"Boot order first entry is '{first_entry}', "
            f"expected type '{expected_type}'"
        )

    def test_power_restore_setting(self, profile):
        expected = (
            profile.get("firmware", {}).get("uefi", {}).get("power_restore")
        )
        if not expected:
            pytest.skip("power_restore not set in profile")

        # This is highly platform-specific. We try a few common paths.
        # For most platforms, override this with a custom_check instead.
        acpi_path = "/sys/firmware/acpi/tables"
        if not os.path.isdir(acpi_path):
            pytest.skip(
                "Cannot verify power_restore automatically on this platform — "
                "use custom_checks in your profile"
            )

        pytest.skip(
            "power_restore automatic check not implemented for this platform — "
            "use custom_checks in your profile to verify the BIOS setting"
        )


# ──────────────────────────────────────────────
# Custom / platform-specific checks
# ──────────────────────────────────────────────

class TestCustomChecks:
    """Run arbitrary shell commands defined in the profile's custom_checks list."""

    def test_custom_checks(self, profile):
        checks = profile.get("custom_checks", [])
        if not checks:
            pytest.skip("No custom_checks defined in profile")

        failures = []
        for check in checks:
            name = check.get("name", check.get("command", "?"))
            cmd = check.get("command")
            if not cmd:
                continue

            expected_rc = check.get("expected_returncode", 0)
            expected_out = check.get("expected_output")  # substring

            try:
                rc, out, err = run_cmd(cmd, timeout=30)
            except Exception as exc:
                failures.append(f"  [{name}] exception: {exc}")
                continue

            if rc != expected_rc:
                failures.append(
                    f"  [{name}] exit code {rc} (expected {expected_rc})\n"
                    f"    cmd: {cmd}\n"
                    f"    stdout: {out!r}\n"
                    f"    stderr: {err!r}"
                )
            elif expected_out is not None and expected_out not in out:
                failures.append(
                    f"  [{name}] stdout does not contain '{expected_out}'\n"
                    f"    cmd: {cmd}\n"
                    f"    stdout: {out!r}"
                )

        if failures:
            pytest.fail(
                f"{len(failures)} custom check(s) failed:\n" + "\n".join(failures)
            )
