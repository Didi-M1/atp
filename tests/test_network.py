"""Network tests: reachability, latency, packet loss, MTU.

All tests use a back-to-back connected peer host whose IP is in the profile.

Profile keys used:
    network.enabled
    network.interface         — local interface (checked for existence)
    network.peer_ip           — IP address of the peer host
    network.mtu               — expected MTU (tested with DF-bit ping)
    network.jumbo_mtu         — if set, also test jumbo frames
    network.ping_count        — number of pings for the statistics tests
    network.max_latency_ms    — max allowed average RTT
    network.max_jitter_ms     — max allowed mdev (jitter); null = skip
    network.max_packet_loss_pct — allowed packet loss %

Requires:
    - ping (BusyBox or iputils-ping)
    - ip (BusyBox)
"""
from __future__ import annotations

import re
from typing import Any

import pytest

from atp.utils import run_cmd, tool_exists

pytestmark = pytest.mark.network


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

@pytest.fixture(scope="module")
def net_cfg(profile) -> dict[str, Any]:
    cfg = profile.get("network", {})
    if not cfg.get("enabled", False):
        pytest.skip("network tests disabled in profile")
    if not cfg.get("peer_ip"):
        pytest.skip("peer_ip not set in profile")
    return cfg


# ──────────────────────────────────────────────
# Interface existence
# ──────────────────────────────────────────────

class TestInterface:

    @pytest.fixture(autouse=True)
    def _require(self, net_cfg):
        pass  # net_cfg fixture already handles skip logic

    def test_interface_exists(self, net_cfg):
        iface = net_cfg.get("interface")
        if not iface:
            pytest.skip("interface not set in profile")

        rc, out, _ = run_cmd(f"ip link show {iface}")
        assert rc == 0, f"Network interface '{iface}' not found"

    def test_interface_mtu_matches_profile(self, net_cfg):
        iface = net_cfg.get("interface")
        expected_mtu = net_cfg.get("mtu", 1500)
        if not iface:
            pytest.skip("interface not set in profile")

        rc, out, _ = run_cmd(f"ip link show {iface}")
        assert rc == 0

        m = re.search(r"mtu (\d+)", out)
        if not m:
            pytest.skip(f"Could not parse MTU from: {out}")

        actual_mtu = int(m.group(1))
        assert actual_mtu == expected_mtu, (
            f"Interface {iface} MTU is {actual_mtu}, expected {expected_mtu}"
        )


# ──────────────────────────────────────────────
# Ping helpers
# ──────────────────────────────────────────────

def _ping(host: str, count: int, size: int | None = None, timeout: int = 60) -> tuple[int, str, str]:
    """Run ping and return (rc, stdout, stderr)."""
    cmd = f"ping -c {count}"
    if size is not None:
        cmd += f" -s {size}"
    cmd += f" {host}"
    return run_cmd(cmd, timeout=timeout)


def _parse_ping_stats(output: str) -> dict[str, float | None]:
    """Parse ping summary line into a dict with keys: loss_pct, min, avg, max, mdev."""
    stats: dict = {}

    # Packet loss: "X% packet loss" or "X packets transmitted, Y received, Z% packet loss"
    m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*packet loss", output)
    if m:
        stats["loss_pct"] = float(m.group(1))

    # RTT line: "rtt min/avg/max/mdev = 0.1/0.2/0.5/0.05 ms"  (iputils)
    # BusyBox:  "round-trip min/avg/max = 0.1/0.2/0.5 ms"
    m = re.search(
        r"(?:rtt|round-trip)\s+min/avg/max(?:/mdev)?\s*=\s*"
        r"([\d.]+)/([\d.]+)/([\d.]+)(?:/([\d.]+))?\s*ms",
        output,
    )
    if m:
        stats["min"] = float(m.group(1))
        stats["avg"] = float(m.group(2))
        stats["max"] = float(m.group(3))
        stats["mdev"] = float(m.group(4)) if m.group(4) else None

    return stats


# ──────────────────────────────────────────────
# Reachability
# ──────────────────────────────────────────────

class TestPingReachability:

    @pytest.fixture(autouse=True)
    def _require(self, net_cfg):
        pass

    def test_peer_reachable(self, net_cfg):
        peer = net_cfg["peer_ip"]
        rc, out, err = _ping(peer, count=3)
        assert rc == 0, (
            f"Cannot reach peer {peer}\nstdout: {out}\nstderr: {err}"
        )


# ──────────────────────────────────────────────
# Latency and packet loss
# ──────────────────────────────────────────────

class TestPingStats:

    @pytest.fixture(autouse=True)
    def _require(self, net_cfg):
        pass

    def test_ping_latency_and_loss(self, net_cfg):
        peer = net_cfg["peer_ip"]
        count = net_cfg.get("ping_count", 100)
        max_latency = net_cfg.get("max_latency_ms")
        max_jitter = net_cfg.get("max_jitter_ms")
        max_loss = net_cfg.get("max_packet_loss_pct", 0.0)

        rc, out, err = _ping(peer, count=count, timeout=count * 2 + 30)
        assert rc == 0 or "packet loss" in out, (
            f"ping to {peer} failed entirely\nstdout: {out}\nstderr: {err}"
        )

        stats = _parse_ping_stats(out)
        if not stats:
            pytest.fail(f"Could not parse ping statistics from:\n{out}")

        errors = []

        loss = stats.get("loss_pct", 0.0)
        if loss > max_loss:
            errors.append(
                f"Packet loss {loss:.1f}% exceeds limit of {max_loss:.1f}%"
            )

        if max_latency is not None:
            avg = stats.get("avg")
            if avg is None:
                errors.append("Could not parse average RTT from ping output")
            elif avg > max_latency:
                errors.append(
                    f"Average RTT {avg:.3f} ms exceeds limit of {max_latency} ms"
                )

        if max_jitter is not None:
            mdev = stats.get("mdev")
            if mdev is None:
                # BusyBox ping doesn't report mdev — skip gracefully
                print(
                    f"\n  NOTE: jitter check skipped (BusyBox ping does not "
                    f"report mdev)"
                )
            elif mdev > max_jitter:
                errors.append(
                    f"Jitter (mdev) {mdev:.3f} ms exceeds limit of {max_jitter} ms"
                )

        if errors:
            pytest.fail(
                f"Ping statistics to {peer} ({count} packets):\n"
                + "\n".join(f"  {e}" for e in errors)
                + f"\n\nRaw ping output:\n{out}"
            )


# ──────────────────────────────────────────────
# MTU
# ──────────────────────────────────────────────

class TestMTU:

    @pytest.fixture(autouse=True)
    def _require(self, net_cfg):
        pass

    def _ping_mtu(self, peer: str, payload_size: int, timeout: int = 10) -> bool:
        """Send a single ping with a specific payload size and DF bit.

        Tries iputils-style -M do first, falls back to raw socket on failure.
        Returns True if the ping succeeded (no fragmentation needed).
        """
        # Try ping -M do (iputils / some BusyBox builds)
        rc, out, err = run_cmd(
            f"ping -c 1 -M do -s {payload_size} -W 2 {peer}",
            timeout=timeout,
        )
        if "invalid" not in err.lower() and "unknown" not in err.lower():
            # -M do was accepted by ping
            return rc == 0

        # Fallback: Python raw socket with DF bit
        return self._raw_ping_df(peer, payload_size)

    @staticmethod
    def _raw_ping_df(peer: str, payload_size: int) -> bool:
        """Send one ICMP echo with DF bit set using raw socket."""
        import socket
        import struct
        import os

        # Requires CAP_NET_RAW (root or appropriate capability)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
        except PermissionError:
            return None  # type: ignore[return-value]  # signal "skip"

        try:
            # IP_MTU_DISCOVER = 10, IP_PMTUDISC_DO = 2  (Linux)
            IP_MTU_DISCOVER = 10
            IP_PMTUDISC_DO = 2
            sock.setsockopt(socket.IPPROTO_IP, IP_MTU_DISCOVER, IP_PMTUDISC_DO)
            sock.settimeout(2)

            # Build a minimal ICMP echo packet
            icmp_type, icmp_code, icmp_id, icmp_seq = 8, 0, os.getpid() & 0xFFFF, 1
            payload = b"A" * payload_size
            header = struct.pack("!BBHHH", icmp_type, icmp_code, 0, icmp_id, icmp_seq)
            packet = header + payload
            checksum = _icmp_checksum(packet)
            header = struct.pack("!BBHHH", icmp_type, icmp_code, checksum, icmp_id, icmp_seq)
            packet = header + payload

            sock.sendto(packet, (peer, 0))
            sock.recvfrom(65535)
            return True
        except OSError:
            return False
        finally:
            sock.close()

    def test_mtu_standard(self, net_cfg):
        peer = net_cfg["peer_ip"]
        mtu = net_cfg.get("mtu", 1500)
        payload = mtu - 28  # 20 B IP header + 8 B ICMP header

        result = self._ping_mtu(peer, payload)
        if result is None:
            pytest.skip(
                "MTU test requires CAP_NET_RAW (run as root) or a ping "
                "binary that supports -M do"
            )
        assert result, (
            f"MTU test FAILED: {payload} B payload (MTU={mtu}) did not reach peer "
            f"{peer} without fragmentation"
        )

    def test_mtu_jumbo(self, net_cfg):
        jumbo_mtu = net_cfg.get("jumbo_mtu")
        if not jumbo_mtu:
            pytest.skip("jumbo_mtu not set in profile")

        peer = net_cfg["peer_ip"]
        payload = jumbo_mtu - 28

        result = self._ping_mtu(peer, payload)
        if result is None:
            pytest.skip("Jumbo MTU test requires CAP_NET_RAW or -M do support")
        assert result, (
            f"Jumbo MTU test FAILED: {payload} B payload (MTU={jumbo_mtu}) "
            f"did not reach peer {peer} without fragmentation"
        )


def _icmp_checksum(data: bytes) -> int:
    """Compute ICMP checksum."""
    s = 0
    for i in range(0, len(data) - 1, 2):
        s += (data[i] << 8) + data[i + 1]
    if len(data) % 2:
        s += data[-1] << 8
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF
