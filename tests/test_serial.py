"""Serial port tests: device existence, open/configure, optional loopback.

Profile keys used:
    serial.enabled
    serial.ports[].device      — device path (/dev/ttyS0, /dev/ttyUSB0, …)
    serial.ports[].baud        — baud rate
    serial.ports[].description — human-readable label
    serial.ports[].loopback    — True if TX/RX are physically shorted (loopback plug)

Loopback test uses Python termios (no pyserial dependency).
"""
from __future__ import annotations

import errno
import os
import termios
import tty
import select

import pytest

from atp.utils import run_cmd

pytestmark = pytest.mark.serial

# Map baud rate integers to termios speed constants
_BAUD_MAP = {
    50:      termios.B50,
    75:      termios.B75,
    110:     termios.B110,
    134:     termios.B134,
    150:     termios.B150,
    200:     termios.B200,
    300:     termios.B300,
    600:     termios.B600,
    1200:    termios.B1200,
    1800:    termios.B1800,
    2400:    termios.B2400,
    4800:    termios.B4800,
    9600:    termios.B9600,
    19200:   termios.B19200,
    38400:   termios.B38400,
    57600:   termios.B57600,
    115200:  termios.B115200,
    230400:  termios.B230400,
    460800:  termios.B460800,
    921600:  termios.B921600,
    1000000: termios.B1000000,
    1500000: termios.B1500000,
    2000000: termios.B2000000,
    3000000: termios.B3000000,
}


def _baud_const(baud: int) -> int:
    """Return the termios speed constant for *baud*, or raise ValueError."""
    const = _BAUD_MAP.get(baud)
    if const is None:
        raise ValueError(f"Unsupported baud rate: {baud}")
    return const


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

@pytest.fixture(scope="module")
def serial_ports(profile) -> list[dict]:
    cfg = profile.get("serial", {})
    if not cfg.get("enabled", False):
        pytest.skip("serial tests disabled in profile")
    ports = cfg.get("ports", [])
    if not ports:
        pytest.skip("No serial ports defined in profile")
    return ports


# ──────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────

class TestSerial:

    @pytest.fixture(autouse=True)
    def _require(self, serial_ports):
        pass  # serial_ports fixture already handles the skip

    def test_devices_exist(self, serial_ports):
        missing = []
        for port in serial_ports:
            device = port["device"]
            if not os.path.exists(device):
                missing.append(f"  {device}  ({port.get('description', '')})")

        if missing:
            pytest.fail(
                "Serial device(s) not found:\n" + "\n".join(missing)
            )

    def test_devices_configurable(self, serial_ports):
        """Verify each serial device can be opened and configured via stty."""
        failures = []
        for port in serial_ports:
            device = port["device"]
            baud = port["baud"]
            desc = port.get("description", device)

            rc, out, err = run_cmd(f"stty -F {device} {baud} raw")
            if rc != 0:
                failures.append(
                    f"  {device} ({desc}): stty failed — {err or out}"
                )

        if failures:
            pytest.fail(
                "Serial port configuration failures:\n" + "\n".join(failures)
            )

    def test_loopback(self, serial_ports):
        """Write bytes and read them back on ports configured with loopback: true."""
        loopback_ports = [p for p in serial_ports if p.get("loopback", False)]
        if not loopback_ports:
            pytest.skip("No ports have loopback: true in profile")

        failures = []
        for port in loopback_ports:
            device = port["device"]
            baud = port["baud"]
            desc = port.get("description", device)

            try:
                result = _loopback_test(device, baud)
                if result is not True:
                    failures.append(f"  {device} ({desc}): {result}")
            except Exception as exc:
                failures.append(f"  {device} ({desc}): exception — {exc}")

        if failures:
            pytest.fail(
                "Serial loopback test failures:\n" + "\n".join(failures)
            )


def _loopback_test(device: str, baud: int, timeout_s: float = 2.0) -> bool | str:  # str = error message
    """Open *device*, write a test pattern, read it back.

    Returns True on success, or an error string on failure.
    """
    TEST_PATTERN = b"ATP_LOOPBACK_TEST\n"

    try:
        fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    except OSError as exc:
        return f"Cannot open: {exc}"

    try:
        # Configure: raw mode, 8N1, no flow control
        attrs = termios.tcgetattr(fd)
        speed = _baud_const(baud)
        attrs[4] = speed   # ispeed
        attrs[5] = speed   # ospeed
        attrs[0] = 0       # iflag: turn off all input processing
        attrs[1] = 0       # oflag: turn off all output processing
        attrs[2] = (       # cflag: 8N1, no flow control
            termios.CS8 | termios.CREAD | termios.CLOCAL
        )
        attrs[3] = 0       # lflag: raw (no canonical, no echo)
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIOFLUSH)

        # Write test pattern
        written = os.write(fd, TEST_PATTERN)
        if written != len(TEST_PATTERN):
            return f"Short write: {written}/{len(TEST_PATTERN)} bytes"

        # Read back with timeout
        buf = b""
        deadline = os.times()[4] + timeout_s  # wall clock via os.times()
        while len(buf) < len(TEST_PATTERN):
            remaining = deadline - os.times()[4]
            if remaining <= 0:
                return (
                    f"Timeout: received {len(buf)}/{len(TEST_PATTERN)} bytes "
                    f"({buf!r})"
                )
            r, _, _ = select.select([fd], [], [], min(remaining, 0.1))
            if r:
                chunk = os.read(fd, len(TEST_PATTERN) - len(buf))
                if chunk:
                    buf += chunk

        if buf == TEST_PATTERN:
            return True
        return f"Data mismatch: sent {TEST_PATTERN!r}, received {buf!r}"

    finally:
        os.close(fd)
