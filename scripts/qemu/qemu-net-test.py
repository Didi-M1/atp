#!/usr/bin/env python3
"""
qemu-net-test.py — Start DUT + peer VMs and run the ATP network tests.

Both VMs are started automatically.  The script logs in to both, verifies
IP connectivity with ping in both directions, then runs the full ATP network
test suite (pytest -m network) on the DUT.

Usage:
    python3 scripts/qemu/qemu-net-test.py [--rootfs PATH] [--kernel PATH]
                                           [--port PORT]

Defaults:
    rootfs  ../../buildroot/output/images/rootfs.ext2
    kernel  ../../buildroot/output/images/bzImage
    port    15555

Requirements:
    pip install pexpect        (usually already available on the host)
"""

import argparse
import os
import subprocess
import sys
import tempfile
import time

try:
    import pexpect
except ImportError:
    sys.exit("ERROR: pexpect not installed.  Run: pip install pexpect")


# ── helpers ───────────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ATP_DIR    = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
BR_IMAGES  = os.path.join(ATP_DIR, "..", "buildroot", "output", "images")

PASSWORD = "root"
BOOT_TIMEOUT = 90   # seconds to wait for login prompt
LOGIN_TIMEOUT = 15
TEST_TIMEOUT  = 300


def kvm_available() -> bool:
    return os.access("/dev/kvm", os.R_OK)


def _login(child: pexpect.spawn, label: str) -> None:
    """Wait for login prompt and authenticate."""
    print(f"[{label}] Waiting for login prompt…")
    child.expect("buildroot login:", timeout=BOOT_TIMEOUT)
    child.sendline("root")
    idx = child.expect(["Password:", r"#\s"], timeout=LOGIN_TIMEOUT)
    if idx == 0:
        child.sendline(PASSWORD)
        child.expect(r"#\s", timeout=LOGIN_TIMEOUT)
    print(f"[{label}] Logged in.")


def _shell(child: pexpect.spawn, cmd: str, timeout: int = 30) -> str:
    """Run a shell command and return its output."""
    marker = f"__END_{id(child)}__"
    child.sendline(f"{cmd}; echo {marker}")
    child.expect(marker, timeout=timeout)
    child.expect(r"#\s", timeout=10)
    # everything between the echoed command and the marker
    return child.before


def _ping(child: pexpect.spawn, target_ip: str, label: str,
          count: int = 5) -> bool:
    """Ping target_ip from VM and return True on success."""
    print(f"[{label}] Pinging {target_ip} ({count} packets)…")
    marker = f"__PING_{id(child)}__"
    child.sendline(f"ping -c {count} {target_ip}; echo {marker}")
    idx = child.expect([marker, pexpect.TIMEOUT], timeout=count * 3 + 10)
    output = child.before
    child.expect(r"#\s", timeout=10)
    if idx != 0:
        print(f"[{label}] TIMEOUT waiting for ping to finish.")
        return False
    success = "0% packet loss" in output or f"{count} received" in output
    print(output.strip())
    return success


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rootfs", default=os.path.join(BR_IMAGES, "rootfs.ext2"),
                        help="Rootfs image path")
    parser.add_argument("--kernel", default=os.path.join(BR_IMAGES, "bzImage"),
                        help="Kernel image path")
    parser.add_argument("--port", type=int, default=15555,
                        help="TCP port for DUT↔peer socket (default: 15555)")
    args = parser.parse_args()

    for path, name in [(args.rootfs, "rootfs"), (args.kernel, "kernel")]:
        if not os.path.isfile(path):
            sys.exit(f"ERROR: {name} not found: {path}\n"
                     f"       Build with:  cd ../buildroot && make atp_qemu_x86_64_defconfig && make -j$(nproc)")

    kvm = "-enable-kvm" if kvm_available() else ""
    cpu = "host" if kvm_available() else "qemu64"

    # ── Detect rootfs format ──────────────────────────────────────────────────
    try:
        info = subprocess.check_output(
            ["qemu-img", "info", "--output=json", args.rootfs],
            stderr=subprocess.DEVNULL, text=True)
        import json
        img_fmt = json.loads(info).get("format", "raw")
    except Exception:
        img_fmt = "raw"

    # ── Temporary qcow2 overlays ──────────────────────────────────────────────
    # Each VM writes to its own overlay; the backing rootfs is opened read-only,
    # so both VMs can access the same file without flock conflicts.
    dut_overlay  = None
    peer_overlay = None
    try:
        dut_overlay  = tempfile.mktemp(suffix=".qcow2", prefix="atp-dut-",  dir="/tmp")
        peer_overlay = tempfile.mktemp(suffix=".qcow2", prefix="atp-peer-", dir="/tmp")
        for overlay in (dut_overlay, peer_overlay):
            subprocess.check_call(
                ["qemu-img", "create", "-f", "qcow2",
                 "-b", args.rootfs, "-F", img_fmt, overlay],
                stdout=subprocess.DEVNULL)
    except Exception as exc:
        sys.exit(f"ERROR: failed to create qcow2 overlay: {exc}")
    print(f"[setup] DUT  overlay: {dut_overlay}")
    print(f"[setup] Peer overlay: {peer_overlay}")

    # ── QEMU command templates ────────────────────────────────────────────────
    BASE = (
        f"qemu-system-x86_64 {kvm} -machine q35 -cpu {cpu} "
        f"-serial mon:stdio -nographic"
    )

    DUT_CMD = (
        f"{BASE} -m 2048 -smp 2 "
        f"-drive file={dut_overlay},format=qcow2,if=virtio "
        f"-netdev socket,id=net0,listen=:{args.port} "
        f"-device e1000,netdev=net0 "
        f"-virtfs local,path={ATP_DIR},mount_tag=atp,security_model=passthrough,id=atp0 "
        f"-device ich9-usb-ehci1,id=ehci1 "
        f"-device ich9-usb-uhci1,id=uhci1,masterbus=ehci1.0,firstport=0 "
        f"-device ich9-usb-uhci2,id=uhci2,masterbus=ehci1.0,firstport=2 "
        f"-device ich9-usb-uhci3,id=uhci3,masterbus=ehci1.0,firstport=4 "
        f"-serial pty "
        f"-kernel {args.kernel} "
        f"-append 'root=/dev/vda rw console=ttyS0,115200 net.ifnames=0 biosdevname=0 "
        f"ip=192.168.200.1::192.168.200.254:255.255.255.0::eth0:off sysrq_always_enabled=1'"
    )

    PEER_CMD = (
        f"{BASE} -m 512 -smp 1 "
        f"-drive file={peer_overlay},format=qcow2,if=virtio "
        f"-netdev socket,id=net0,connect=127.0.0.1:{args.port} "
        f"-device e1000,netdev=net0 "
        f"-kernel {args.kernel} "
        f"-append 'root=/dev/vda rw console=ttyS0,115200 net.ifnames=0 biosdevname=0 "
        f"ip=192.168.200.2::192.168.200.254:255.255.255.0::eth0:off'"
    )

    dut = peer = None
    try:
        # ── Start DUT (listens first) ─────────────────────────────────────────
        print(f"\n[DUT] Starting VM (IP 192.168.200.1, port {args.port})…")
        dut = pexpect.spawn(DUT_CMD, timeout=BOOT_TIMEOUT, encoding="utf-8")
        dut.logfile_read = sys.stdout

        # Give the DUT a few seconds to open the socket before peer connects
        time.sleep(2)

        # ── Start peer ────────────────────────────────────────────────────────
        print(f"\n[PEER] Starting VM (IP 192.168.200.2)…")
        peer = pexpect.spawn(PEER_CMD, timeout=BOOT_TIMEOUT, encoding="utf-8")
        peer.logfile_read = sys.stdout

        # ── Wait for both to boot ─────────────────────────────────────────────
        _login(dut,  "DUT")
        _login(peer, "PEER")

        # ── Check IPs ─────────────────────────────────────────────────────────
        print("\n[DUT]  IP address:")
        _shell(dut,  "ip addr show eth0")
        print("\n[PEER] IP address:")
        _shell(peer, "ip addr show eth0")

        # ── Ping DUT → peer ───────────────────────────────────────────────────
        print("\n" + "="*60)
        print("PING DUT → PEER (192.168.200.1 → 192.168.200.2)")
        print("="*60)
        ok_dut_to_peer = _ping(dut, "192.168.200.2", "DUT")

        # ── Ping peer → DUT ───────────────────────────────────────────────────
        print("\n" + "="*60)
        print("PING PEER → DUT (192.168.200.2 → 192.168.200.1)")
        print("="*60)
        ok_peer_to_dut = _ping(peer, "192.168.200.1", "PEER")

        # ── Run ATP network test suite on DUT ─────────────────────────────────
        print("\n" + "="*60)
        print("ATP NETWORK TEST SUITE  (pytest -m network)")
        print("="*60)
        dut.logfile_read = None  # suppress raw output during pytest
        marker = "__ATP_DONE__"
        dut.sendline(
            f"cd /mnt/atp && "
            f"python3 -m pytest tests/test_network.py --profile=example_qemu "
            f"-v --tb=short 2>&1; echo {marker}"
        )
        idx = dut.expect([marker, pexpect.TIMEOUT], timeout=TEST_TIMEOUT)
        pytest_output = dut.before
        if idx == 0:
            dut.expect(r"#\s", timeout=30)
        print(pytest_output)
        dut.logfile_read = sys.stdout

        # ── Summary ───────────────────────────────────────────────────────────
        print("\n" + "="*60)
        print("SUMMARY")
        print("="*60)
        print(f"  DUT  → PEER ping: {'PASS' if ok_dut_to_peer  else 'FAIL'}")
        print(f"  PEER → DUT  ping: {'PASS' if ok_peer_to_dut  else 'FAIL'}")
        pytest_ok = idx == 0 and ("passed" in pytest_output or "no tests ran" in pytest_output)
        print(f"  ATP network tests: {'PASS' if pytest_ok else 'FAIL / TIMEOUT'}")

        # ── Shutdown ──────────────────────────────────────────────────────────
        for vm, label in [(dut, "DUT"), (peer, "PEER")]:
            vm.sendline("poweroff")
            vm.expect(pexpect.EOF, timeout=30)
            print(f"[{label}] Shut down.")

        return 0 if (ok_dut_to_peer and ok_peer_to_dut and pytest_ok) else 1

    except KeyboardInterrupt:
        print("\n[interrupted]")
        return 1
    finally:
        for vm in (dut, peer):
            if vm and vm.isalive():
                vm.terminate(force=True)
        for overlay in (dut_overlay, peer_overlay):
            if overlay:
                try:
                    os.unlink(overlay)
                except OSError:
                    pass


if __name__ == "__main__":
    sys.exit(main())
