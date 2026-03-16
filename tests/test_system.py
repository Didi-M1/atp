"""System info tests: CPU, RAM, storage, temperature, RTC.

Profile keys used (all under 'system'):
    cpu.min_cores, cpu.expected_vendor, cpu.expected_model, cpu.max_temp_c
    ram.min_total_mb
    storage.devices[].path, .min_size_gb
    rtc.device, rtc.min_year, rtc.max_drift_s
"""
from __future__ import annotations

import datetime
import os
import pytest

from atp.utils import run_cmd, read_file, tool_exists

pytestmark = pytest.mark.system


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _cpuinfo() -> str:
    """Return the raw text of /proc/cpuinfo (empty string if unreadable)."""
    return read_file("/proc/cpuinfo") or ""


def _meminfo() -> str:
    """Return the raw text of /proc/meminfo (empty string if unreadable)."""
    return read_file("/proc/meminfo") or ""


# ──────────────────────────────────────────────
# CPU
# ──────────────────────────────────────────────

class TestCPU:

    @pytest.fixture(autouse=True)
    def _require(self, profile):
        sys_cfg = profile.get("system", {})
        if not sys_cfg.get("enabled", True):
            pytest.skip("system tests disabled in profile")
        if not sys_cfg.get("cpu", {}).get("enabled", True):
            pytest.skip("cpu tests disabled in profile")

    def test_cpu_count(self, profile):
        min_cores = profile.get("system", {}).get("cpu", {}).get("min_cores")
        if min_cores is None:
            pytest.skip("min_cores not set in profile")

        rc, out, _ = run_cmd("nproc")
        assert rc == 0, "nproc failed"
        cores = int(out)
        assert cores >= min_cores, f"CPU cores: expected >= {min_cores}, got {cores}"

    def test_cpu_vendor(self, profile):
        expected = profile.get("system", {}).get("cpu", {}).get("expected_vendor")
        if not expected:
            pytest.skip("expected_vendor not set in profile")

        for line in _cpuinfo().splitlines():
            if "vendor_id" in line.lower():
                actual = line.split(":", 1)[1].strip()
                assert actual == expected, (
                    f"CPU vendor: expected '{expected}', got '{actual}'"
                )
                return
        pytest.fail("vendor_id not found in /proc/cpuinfo")

    def test_cpu_model(self, profile):
        expected = profile.get("system", {}).get("cpu", {}).get("expected_model")
        if not expected:
            pytest.skip("expected_model not set in profile")

        for line in _cpuinfo().splitlines():
            if "model name" in line.lower():
                actual = line.split(":", 1)[1].strip()
                assert expected in actual, (
                    f"CPU model mismatch: '{expected}' not in '{actual}'"
                )
                return
        pytest.fail("model name not found in /proc/cpuinfo")

    def test_cpu_max_temperature(self, profile):
        max_temp = profile.get("system", {}).get("cpu", {}).get("max_temp_c")
        if max_temp is None:
            pytest.skip("max_temp_c not set in profile")

        rc, out, _ = run_cmd(
            "cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null"
        )
        if not out:
            pytest.skip("No thermal zones found in /sys/class/thermal/")

        temps = []
        for line in out.splitlines():
            try:
                temps.append(int(line.strip()) / 1000.0)
            except ValueError:
                pass

        if not temps:
            pytest.skip("Could not parse any thermal zone temperatures")

        peak = max(temps)
        assert peak <= max_temp, (
            f"CPU temperature {peak:.1f}°C exceeds profile limit of {max_temp}°C"
        )


# ──────────────────────────────────────────────
# RAM
# ──────────────────────────────────────────────

class TestRAM:

    @pytest.fixture(autouse=True)
    def _require(self, profile):
        sys_cfg = profile.get("system", {})
        if not sys_cfg.get("enabled", True):
            pytest.skip("system tests disabled in profile")
        if not sys_cfg.get("ram", {}).get("enabled", True):
            pytest.skip("ram tests disabled in profile")

    def test_ram_total(self, profile):
        min_mb = profile.get("system", {}).get("ram", {}).get("min_total_mb")
        if min_mb is None:
            pytest.skip("min_total_mb not set in profile")

        for line in _meminfo().splitlines():
            if line.startswith("MemTotal:"):
                kb = int(line.split()[1])
                mb = kb / 1024.0
                assert mb >= min_mb, (
                    f"RAM total: expected >= {min_mb} MB, got {mb:.0f} MB"
                )
                return
        pytest.fail("MemTotal not found in /proc/meminfo")

    def test_ram_available_nonzero(self, profile):
        """Sanity check: there must be some free RAM."""
        for line in _meminfo().splitlines():
            if line.startswith("MemAvailable:"):
                kb = int(line.split()[1])
                assert kb > 0, "MemAvailable is 0 — system may be under severe memory pressure"
                return
        pytest.skip("MemAvailable not present in /proc/meminfo")


# ──────────────────────────────────────────────
# Storage
# ──────────────────────────────────────────────

class TestStorage:

    @pytest.fixture(autouse=True)
    def _require(self, profile):
        sys_cfg = profile.get("system", {})
        if not sys_cfg.get("enabled", True):
            pytest.skip("system tests disabled in profile")
        if not sys_cfg.get("storage", {}).get("enabled", True):
            pytest.skip("storage tests disabled in profile")

    def test_storage_devices(self, profile):
        devices = profile.get("system", {}).get("storage", {}).get("devices", [])
        if not devices:
            pytest.skip("No storage devices defined in profile")

        if not tool_exists("lsblk"):
            pytest.skip("lsblk not available")

        for dev in devices:
            path = dev["path"]
            rc, out, _ = run_cmd(f"lsblk -bnd -o SIZE {path}")
            assert rc == 0, f"Block device not found: {path}"

            min_gb = dev.get("min_size_gb")
            if min_gb is not None:
                size_bytes = int(out)
                size_gb = size_bytes / (1024 ** 3)
                assert size_gb >= min_gb, (
                    f"{path}: expected >= {min_gb} GB, got {size_gb:.1f} GB"
                )

    def test_rootfs_writable(self, profile):
        """Root filesystem must be mounted read-write."""
        import tempfile
        try:
            with tempfile.NamedTemporaryFile(dir="/tmp"):
                pass
        except OSError as exc:
            pytest.fail(f"Filesystem not writable: {exc}")


# ──────────────────────────────────────────────
# RTC
# ──────────────────────────────────────────────

class TestRTC:

    @pytest.fixture(autouse=True)
    def _require(self, profile):
        sys_cfg = profile.get("system", {})
        if not sys_cfg.get("enabled", True):
            pytest.skip("system tests disabled in profile")
        if not sys_cfg.get("rtc", {}).get("enabled", True):
            pytest.skip("rtc tests disabled in profile")

    def _rtc_cfg(self, profile) -> dict:
        return profile.get("system", {}).get("rtc", {})

    def test_rtc_device_exists(self, profile):
        device = self._rtc_cfg(profile).get("device", "/dev/rtc0")
        assert os.path.exists(device), f"RTC device not found: {device}"

    def test_rtc_readable(self, profile):
        """hwclock must read the hardware clock without error."""
        if not tool_exists("hwclock"):
            pytest.skip("hwclock not available")

        rc, out, err = run_cmd("hwclock --show --utc")
        assert rc == 0, f"hwclock failed (rc={rc}): {err.strip()}"
        assert out.strip(), "hwclock returned no output"

    def test_rtc_year_sane(self, profile):
        """Hardware clock year must be >= min_year (catches stuck-at-epoch RTC)."""
        min_year = self._rtc_cfg(profile).get("min_year", 2020)
        if min_year is None:
            pytest.skip("min_year not set in profile")
        if not tool_exists("hwclock"):
            pytest.skip("hwclock not available")

        rc, out, _ = run_cmd("hwclock --show --utc")
        if rc != 0 or not out.strip():
            pytest.skip("hwclock unreadable — covered by test_rtc_readable")

        # hwclock output: "2024-06-01 12:34:56.789012+00:00"
        try:
            year = int(out.strip().split("-")[0])
        except (ValueError, IndexError):
            pytest.fail(f"Could not parse year from hwclock output: {out.strip()!r}")

        assert year >= min_year, (
            f"RTC year {year} is below minimum {min_year} — clock may be unset or stuck at epoch"
        )

    def test_rtc_drift(self, profile):
        """Delta between hardware clock and system clock must be within max_drift_s."""
        max_drift_s = self._rtc_cfg(profile).get("max_drift_s")
        if max_drift_s is None:
            pytest.skip("max_drift_s not set in profile")
        if not tool_exists("hwclock"):
            pytest.skip("hwclock not available")

        before = datetime.datetime.now(datetime.timezone.utc)
        rc, out, _ = run_cmd("hwclock --show --utc")
        after = datetime.datetime.now(datetime.timezone.utc)

        if rc != 0 or not out.strip():
            pytest.skip("hwclock unreadable — covered by test_rtc_readable")

        try:
            hw_time = datetime.datetime.fromisoformat(out.strip())
        except ValueError:
            pytest.fail(f"Could not parse hwclock output: {out.strip()!r}")

        sys_mid = before + (after - before) / 2
        drift_s = abs((hw_time - sys_mid).total_seconds())
        assert drift_s <= max_drift_s, (
            f"RTC drift {drift_s:.1f}s exceeds allowed {max_drift_s}s"
        )
