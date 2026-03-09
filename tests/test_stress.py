"""Stress tests: CPU throughput, RAM fill/verify, disk write/read, GPU (glmark2).

Profile keys used (all under 'stress'):
    enabled
    duration_s
    cpu.enabled, cpu.workers, cpu.min_throughput_mbps
    ram.enabled, ram.size_mb, ram.min_write_mbps, ram.min_read_mbps
    disk.enabled, disk.path, disk.size_mb, disk.min_write_mbps, disk.min_read_mbps
    gpu.enabled, gpu.min_score

CPU temperature after stress is checked against system.cpu.max_temp_c (if set).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time

import pytest

from atp.utils import read_file, run_cmd, tool_exists

pytestmark = pytest.mark.stress

# ── constants ─────────────────────────────────────────────────────────────────

_CHUNK = 1 << 20  # 1 MiB — I/O block size used throughout

## @brief Python script run in each CPU worker subprocess.
#
#  Receives the stress duration (seconds) as sys.argv[1].
#  Hashes 1 MiB chunks of data until the deadline, then prints the
#  total number of bytes processed to stdout.
_CPU_WORKER = """\
import sys, hashlib, time
chunk = b"A" * (1 << 20)
total = 0
deadline = time.monotonic() + float(sys.argv[1])
while time.monotonic() < deadline:
    hashlib.sha256(chunk).digest()
    total += 1 << 20
print(total)
"""


# ── shared helpers ────────────────────────────────────────────────────────────

def _stress_cfg(profile: dict) -> dict:
    """
    @brief Return the 'stress' sub-dict from a profile (empty dict if absent).
    @param profile  The fully-resolved ATP profile dict.
    @return Dict containing the stress configuration keys.
    """
    return profile.get("stress", {})


def _mem_total_mb() -> int | None:
    """
    @brief Read MemTotal from /proc/meminfo.
    @return Total RAM in megabytes, or None if the file is unreadable.
    """
    meminfo = read_file("/proc/meminfo") or ""
    for line in meminfo.splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) // 1024
    return None


def _cpu_temp() -> float | None:
    """
    @brief Read the maximum temperature across all thermal zones.
    @return Peak temperature in °C, or None if no thermal zones are readable.
    """
    _, out, _ = run_cmd("cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null")
    temps = []
    for line in out.splitlines():
        try:
            temps.append(int(line.strip()) / 1000.0)
        except ValueError:
            pass
    return max(temps) if temps else None


# ── CPU stress ────────────────────────────────────────────────────────────────

class TestCPUStress:

    @pytest.fixture(autouse=True)
    def _require(self, profile):
        cfg = _stress_cfg(profile)
        if not cfg.get("enabled", False):
            pytest.skip("stress tests disabled in profile")
        if not cfg.get("cpu", {}).get("enabled", False):
            pytest.skip("CPU stress test disabled in profile")

    def test_cpu_stress_throughput(self, profile):
        """
        @brief Stress all CPU cores with parallel SHA-256 workers.

        Spawns one Python subprocess per configured worker, each hashing
        1 MiB chunks for duration_s seconds.  Reports aggregate throughput
        and optionally asserts it meets min_throughput_mbps.
        """
        cfg        = _stress_cfg(profile)
        cpu_cfg    = cfg.get("cpu", {})
        duration_s = cfg.get("duration_s", 30)
        workers    = cpu_cfg.get("workers") or os.cpu_count() or 1
        min_mbps   = cpu_cfg.get("min_throughput_mbps")

        procs = [
            subprocess.Popen(
                [sys.executable, "-c", _CPU_WORKER, str(duration_s)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for _ in range(workers)
        ]

        total_bytes = 0
        for p in procs:
            try:
                out, _ = p.communicate(timeout=duration_s + 30)
                total_bytes += int(out.strip())
            except subprocess.TimeoutExpired:
                p.kill()
                p.communicate()
                pytest.fail(
                    f"CPU worker process hung — did not exit within "
                    f"{duration_s + 30}s"
                )
            except (ValueError, TypeError):
                pass

        throughput_mbps = (total_bytes / _CHUNK) / duration_s

        if min_mbps is not None:
            assert throughput_mbps >= min_mbps, (
                f"CPU stress throughput {throughput_mbps:.1f} MB/s "
                f"< required {min_mbps} MB/s"
            )

    def test_cpu_temperature_after_stress(self, profile):
        """
        @brief Verify CPU temperature stays within the profile limit after stress.

        Reads the peak thermal-zone temperature and compares it against
        system.cpu.max_temp_c.  Skipped if that key is null.
        """
        max_temp = profile.get("system", {}).get("cpu", {}).get("max_temp_c")
        if max_temp is None:
            pytest.skip("max_temp_c not set in profile")

        temp = _cpu_temp()
        if temp is None:
            pytest.skip("No thermal zones readable")

        assert temp <= max_temp, (
            f"CPU temperature {temp:.1f}°C exceeds limit {max_temp}°C "
            f"after stress run"
        )


# ── RAM stress ────────────────────────────────────────────────────────────────

class TestRAMStress:

    @pytest.fixture(autouse=True)
    def _require(self, profile):
        cfg = _stress_cfg(profile)
        if not cfg.get("enabled", False):
            pytest.skip("stress tests disabled in profile")
        if not cfg.get("ram", {}).get("enabled", False):
            pytest.skip("RAM stress test disabled in profile")

    def test_ram_stress_throughput(self, profile):
        """
        @brief Fill a large buffer with a byte pattern, verify it, measure throughput.

        Allocates size_mb of RAM as a bytearray, writes an 0xAA pattern in
        1 MiB chunks, then reads it back and verifies each chunk.  Reports
        write and read+verify throughput.  Fails on MemoryError or data
        corruption.
        """
        cfg       = _stress_cfg(profile)
        ram_cfg   = cfg.get("ram", {})
        min_write = ram_cfg.get("min_write_mbps")
        min_read  = ram_cfg.get("min_read_mbps")

        size_mb = ram_cfg.get("size_mb")
        if size_mb is None:
            total = _mem_total_mb()
            size_mb = max(64, total // 4) if total else 64

        size_bytes  = size_mb * _CHUNK
        pattern     = bytes([0xAA]) * _CHUNK

        try:
            buf = bytearray(size_bytes)
        except MemoryError:
            pytest.fail(
                f"Could not allocate {size_mb} MB for RAM stress — "
                f"reduce stress.ram.size_mb in the profile"
            )

        # Write phase: fill in 1 MiB chunks
        t0 = time.monotonic()
        for i in range(0, size_bytes, _CHUNK):
            end = min(i + _CHUNK, size_bytes)
            buf[i:end] = pattern[:end - i]
        write_mbps = size_mb / (time.monotonic() - t0)

        # Read + verify phase: compare each chunk against the pattern
        corrupted = []
        t0 = time.monotonic()
        for i in range(0, size_bytes, _CHUNK):
            end = min(i + _CHUNK, size_bytes)
            if buf[i:end] != pattern[:end - i]:
                corrupted.append(i)
        read_mbps = size_mb / (time.monotonic() - t0)

        assert not corrupted, (
            f"RAM corruption detected at {len(corrupted)} chunk offset(s): "
            f"{corrupted[:5]}{'…' if len(corrupted) > 5 else ''} — "
            f"possible memory hardware fault"
        )

        if min_write is not None:
            assert write_mbps >= min_write, (
                f"RAM write throughput {write_mbps:.1f} MB/s "
                f"< required {min_write} MB/s"
            )
        if min_read is not None:
            assert read_mbps >= min_read, (
                f"RAM read+verify throughput {read_mbps:.1f} MB/s "
                f"< required {min_read} MB/s"
            )


# ── disk stress ───────────────────────────────────────────────────────────────

class TestDiskStress:

    @pytest.fixture(autouse=True)
    def _require(self, profile):
        cfg = _stress_cfg(profile)
        if not cfg.get("enabled", False):
            pytest.skip("stress tests disabled in profile")
        if not cfg.get("disk", {}).get("enabled", False):
            pytest.skip("disk stress test disabled in profile")

    def test_disk_stress_throughput(self, profile):
        """
        @brief Write and read back a large file to measure disk throughput.

        Writes size_mb MiB of zero bytes with os.fsync to ensure data hits
        the storage medium, then reads the file back in 1 MiB chunks.
        The test file is always removed in a finally block.  Optionally
        asserts write/read throughput against configured thresholds.
        """
        cfg       = _stress_cfg(profile)
        disk_cfg  = cfg.get("disk", {})
        disk_path = disk_cfg.get("path", "/tmp")
        size_mb   = disk_cfg.get("size_mb", 512)
        min_write = disk_cfg.get("min_write_mbps")
        min_read  = disk_cfg.get("min_read_mbps")

        # Unique filename to avoid collisions with concurrent runs
        test_file  = os.path.join(disk_path, f"atp_stress_{os.getpid()}.tmp")
        zero_chunk = b"\x00" * _CHUNK

        try:
            # Write phase
            t0 = time.monotonic()
            with open(test_file, "wb") as f:
                for _ in range(size_mb):
                    f.write(zero_chunk)
                f.flush()
                os.fsync(f.fileno())        # flush OS page cache to device
            write_mbps = size_mb / (time.monotonic() - t0)

            # Read phase
            t0 = time.monotonic()
            with open(test_file, "rb") as f:
                while f.read(_CHUNK):
                    pass
            read_mbps = size_mb / (time.monotonic() - t0)

        except OSError as exc:
            pytest.fail(f"Disk stress I/O error on '{disk_path}': {exc}")
        finally:
            try:
                os.unlink(test_file)
            except OSError:
                pass

        if min_write is not None:
            assert write_mbps >= min_write, (
                f"Disk write throughput {write_mbps:.1f} MB/s "
                f"< required {min_write} MB/s  (path: {disk_path})"
            )
        if min_read is not None:
            assert read_mbps >= min_read, (
                f"Disk read throughput {read_mbps:.1f} MB/s "
                f"< required {min_read} MB/s  (path: {disk_path})"
            )


# ── GPU stress ────────────────────────────────────────────────────────────────

class TestGPUStress:

    @pytest.fixture(autouse=True)
    def _require(self, profile):
        cfg = _stress_cfg(profile)
        if not cfg.get("enabled", False):
            pytest.skip("stress tests disabled in profile")
        if not cfg.get("gpu", {}).get("enabled", False):
            pytest.skip("GPU stress test disabled in profile")

    def test_gpu_stress_glmark2(self, profile):
        """
        @brief Run glmark2 off-screen and check the resulting score.

        Requires glmark2 to be installed.  Runs in off-screen mode so no
        display server is needed.  Parses the 'glmark2 Score: N' line from
        stdout and optionally asserts it meets min_score.
        """
        if not tool_exists("glmark2"):
            pytest.skip(
                "glmark2 not installed — install it to enable GPU stress "
                "(e.g. apt install glmark2)"
            )

        cfg        = _stress_cfg(profile)
        duration_s = cfg.get("duration_s", 30)
        min_score  = cfg.get("gpu", {}).get("min_score")

        rc, out, err = run_cmd(
            f"glmark2 --off-screen --duration={duration_s}",
            timeout=duration_s + 60,
        )
        if rc != 0:
            pytest.fail(f"glmark2 exited with code {rc}:\n{err or out}")

        m = re.search(r"glmark2\s+Score:\s*(\d+)", out, re.IGNORECASE)
        if not m:
            pytest.fail(
                f"Could not parse glmark2 score from output:\n{out}"
            )

        score = int(m.group(1))
        if min_score is not None:
            assert score >= min_score, (
                f"GPU glmark2 score {score} < required {min_score}"
            )
