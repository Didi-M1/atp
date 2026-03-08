"""System info tests: CPU, RAM, storage, temperature.

Profile keys used (all under 'system'):
    cpu.min_cores, cpu.expected_vendor, cpu.expected_model, cpu.max_temp_c
    ram.min_total_mb
    storage.devices[].path, .min_size_gb
"""
from __future__ import annotations

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
