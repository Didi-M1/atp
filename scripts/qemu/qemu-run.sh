#!/bin/sh
# qemu-run.sh — Launch the ATP Device Under Test (DUT) QEMU/KVM VM
#
# Usage:
#   ./scripts/qemu/qemu-run.sh [ROOTFS_IMG] [--kernel BZIMAGE] [--port PORT]
#                              [--profile PROFILE]
#
# Arguments:
#   ROOTFS_IMG   Root filesystem image (raw or qcow2).
#                Default: ../../buildroot/output/images/rootfs.ext2
#   --kernel     Kernel image for direct boot (sets eth0 IP via cmdline).
#                Default: ../../buildroot/output/images/bzImage
#   --port       UDP multicast port shared with the peer VM.
#                Default: 15555
#   --profile    ATP profile name to run automatically after boot.
#                Writes a trigger file; the guest init script picks it up
#                and runs: cd /mnt/atp && pytest --profile=PROFILE
#
# Environment variables:
#   QEMU_RAM   RAM in MiB         (default: 2048)
#   QEMU_CPU   CPU model          (default: "host" with KVM, "qemu64" without)
#   QEMU_SMP   vCPU count         (default: 2)
#
# The VM provides:
#   ttyS0   → this terminal (guest console + QEMU monitor via Ctrl-a c)
#   ttyS1   → PTY on host  (ATP serial test device — path printed at startup)
#   eth0    → 192.168.200.1, UDP multicast segment shared with peer VM
#   /dev/vda → virtio-blk disk from ROOTFS_IMG
#   9p tag "atp" → ATP project directory (auto-mounted at /mnt/atp on boot)
#
# After the VM boots (guest commands):
#   cd /mnt/atp && pytest --profile=example_qemu
#
# Start the peer VM in a second terminal:
#   ./scripts/qemu/qemu-peer.sh [ROOTFS_IMG] [--kernel BZIMAGE]
#
# Exit QEMU: press Ctrl-a x   (or Ctrl-a c for monitor, then "quit")

set -eu

# ── Default image paths (buildroot output) ─────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ATP_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BR_IMAGES="${ATP_DIR}/../buildroot/output/images"
DEFAULT_ROOTFS="${BR_IMAGES}/rootfs.ext2"
DEFAULT_KERNEL="${BR_IMAGES}/bzImage"

# ── Arguments ──────────────────────────────────────────────────────────────────
ROOTFS_IMG="${1:-}"
KERNEL=""
SOCKET_PORT="15555"
PROFILE=""

# First positional arg may be the rootfs image; skip if it starts with '--'
case "${ROOTFS_IMG}" in
    --*|"") ROOTFS_IMG="" ;;
    *)      shift 2>/dev/null || true ;;
esac

while [ $# -gt 0 ]; do
    case "$1" in
        --kernel)  KERNEL="$2";      shift 2 ;;
        --port)    SOCKET_PORT="$2"; shift 2 ;;
        --profile) PROFILE="$2";     shift 2 ;;
        -h|--help) sed -n '2,/^[^#]/{ /^#/p }' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

# Apply defaults
if [ -z "$ROOTFS_IMG" ]; then
    ROOTFS_IMG="$DEFAULT_ROOTFS"
fi
if [ -z "$KERNEL" ] && [ -f "$DEFAULT_KERNEL" ]; then
    KERNEL="$DEFAULT_KERNEL"
fi

[ -f "$ROOTFS_IMG" ] || { echo "ERROR: rootfs image not found: $ROOTFS_IMG" >&2; exit 1; }
[ -n "$KERNEL" ]     || { echo "ERROR: no kernel found. Pass --kernel BZIMAGE or build with buildroot." >&2; exit 1; }
[ -f "$KERNEL" ]     || { echo "ERROR: kernel not found: $KERNEL" >&2; exit 1; }

# ── KVM ────────────────────────────────────────────────────────────────────────
if [ -e /dev/kvm ] && [ -r /dev/kvm ]; then
    KVM="-enable-kvm"
    CPU_DEFAULT="host"
    echo "[qemu-run] KVM: enabled"
else
    KVM=""
    CPU_DEFAULT="qemu64"
    echo "[qemu-run] KVM: not available — software emulation (slow)"
fi

QEMU_RAM="${QEMU_RAM:-2048}"
QEMU_CPU="${QEMU_CPU:-$CPU_DEFAULT}"
QEMU_SMP="${QEMU_SMP:-2}"

# ── Image format ───────────────────────────────────────────────────────────────
if command -v qemu-img >/dev/null 2>&1; then
    IMG_FMT=$(qemu-img info "$ROOTFS_IMG" 2>/dev/null \
        | sed -n 's/^file format: //p')
fi
IMG_FMT="${IMG_FMT:-raw}"
echo "[qemu-run] Rootfs: $(basename "$ROOTFS_IMG")  format=$IMG_FMT"
echo "[qemu-run] Kernel: $(basename "$KERNEL")"

# ── Kernel cmdline ─────────────────────────────────────────────────────────────
# ip= format: <client-ip>:<server-ip>:<gw-ip>:<netmask>:<hostname>:<device>:<autoconf>
KERNEL_APPEND="root=/dev/vda rw console=ttyS0,115200 net.ifnames=0 biosdevname=0 ip=192.168.200.1::192.168.200.254:255.255.255.0::eth0:off sysrq_always_enabled=1"

# ── Info ───────────────────────────────────────────────────────────────────────
echo ""
echo "---------------------------------------------------------------"
printf "  ATP DUT VM   RAM=%s MiB   CPU=%s   vCPUs=%s\n" \
    "$QEMU_RAM" "$QEMU_CPU" "$QEMU_SMP"
echo "  DUT IP:   192.168.200.1  (eth0)"
echo "  Network:  UDP multicast 230.0.0.1:${SOCKET_PORT} (shared with peer)"
echo "  ttyS0  →  this terminal (guest console)"
echo "  ttyS1  →  PTY  (path printed by QEMU on the next line)"
echo "  9p tag:   atp → auto-mounted at /mnt/atp on boot"
if [ -n "$PROFILE" ]; then
    echo "  Auto-login: root / root"
    echo "  Tests:      pytest --profile=${PROFILE}"
else
    echo "  Run tests: cd /mnt/atp && pytest --profile=example_qemu"
fi
echo "---------------------------------------------------------------"
echo ""

# ── Temporary qcow2 overlay ────────────────────────────────────────────────────
# Create a per-run copy-on-write overlay so the raw rootfs is never write-locked.
# This lets a peer VM open the same backing file simultaneously.
OVERLAY=$(mktemp /tmp/atp-dut-XXXXXX.qcow2)
trap 'rm -f "$OVERLAY"' EXIT INT TERM
qemu-img create -f qcow2 -b "$ROOTFS_IMG" -F "$IMG_FMT" "$OVERLAY" >/dev/null
echo "[qemu-run] Overlay: $OVERLAY"

# ── Build argument list ────────────────────────────────────────────────────────
set --
set -- "$@" qemu-system-x86_64
[ -n "$KVM" ] && set -- "$@" $KVM
set -- "$@" -machine q35
set -- "$@" -cpu   "$QEMU_CPU"
set -- "$@" -m     "$QEMU_RAM"
set -- "$@" -smp   "$QEMU_SMP"
# Disk: write to qcow2 overlay; backing file (rootfs.ext2) is opened read-only
set -- "$@" -drive "file=${OVERLAY},format=qcow2,if=virtio"
# Network: UDP multicast — both VMs join the same group on localhost.
# No TCP connection to establish, so there is no timing race between DUT
# and peer startup.  Use a different port per test session to avoid conflicts.
set -- "$@" -netdev "socket,id=net0,mcast=230.0.0.1:${SOCKET_PORT},localaddr=127.0.0.1"
set -- "$@" -device "e1000,netdev=net0"
# virtio-9p: exports ATP directory as tag "atp"
set -- "$@" -virtfs "local,path=${ATP_DIR},mount_tag=atp,security_model=passthrough,id=atp0"
# USB: ICH9 EHCI + companion UHCI controllers (required for ATP USB peripheral test)
# QEMU q35 does not enable USB by default — add them explicitly.
set -- "$@" -device "ich9-usb-ehci1,id=ehci1"
set -- "$@" -device "ich9-usb-uhci1,id=uhci1,masterbus=ehci1.0,firstport=0"
set -- "$@" -device "ich9-usb-uhci2,id=uhci2,masterbus=ehci1.0,firstport=2"
set -- "$@" -device "ich9-usb-uhci3,id=uhci3,masterbus=ehci1.0,firstport=4"
# Serial 0: guest console on stdio (with QEMU monitor multiplexed via Ctrl-a c)
set -- "$@" -serial mon:stdio
# Serial 1: PTY on host — QEMU prints the /dev/pts/X path at startup
set -- "$@" -serial pty
# Headless (no graphical display)
set -- "$@" -nographic
# Direct kernel boot (always used — required for ip= cmdline parameter)
set -- "$@" -kernel "$KERNEL"
set -- "$@" -append "$KERNEL_APPEND"

# ── Launch ─────────────────────────────────────────────────────────────────────
if [ -n "$PROFILE" ]; then
    # Auto-login and run tests via expect
    command -v expect >/dev/null 2>&1 || {
        echo "ERROR: 'expect' is required for --profile. Install: sudo apt install expect" >&2
        exit 1
    }

    # Tell atp-reboot-continue.sh to step aside — expect owns the console and
    # will run the stability continuation in the foreground after each reboot.
    touch "${ATP_DIR}/.atp-expect-mode"

    # Write the QEMU args to a temp wrapper script that expect can spawn.
    # Single-quote each arg, escaping any internal single quotes.
    QEMU_WRAPPER=$(mktemp /tmp/atp-qemu-XXXXXX.sh)
    # Write the expect script.
    # The heredoc expands shell variables $QEMU_WRAPPER and ${PROFILE}.
    # Tcl variables ($rebooted) must be written as \$rebooted so the shell
    # does not expand them — they arrive in the .tcl file as plain $rebooted.
    EXPECT_SCRIPT=$(mktemp /tmp/atp-expect-XXXXXX.tcl)
    trap 'rm -f "$OVERLAY" "$QEMU_WRAPPER" "$EXPECT_SCRIPT" "${ATP_DIR}/.atp-expect-mode"' EXIT INT TERM
    {
        printf '#!/bin/sh\nexec'
        for arg in "$@"; do
            printf " '%s'" "$(printf '%s' "$arg" | sed "s/'/'\\\\''/g")"
        done
        printf '\n'
    } > "$QEMU_WRAPPER"
    chmod +x "$QEMU_WRAPPER"

    cat > "$EXPECT_SCRIPT" << EXPECT_EOF
log_user 1
set timeout 120
spawn $QEMU_WRAPPER

# ── Boot and login ───────────────────────────────────────────────────────
expect {
    "buildroot login:" { send "root\r"; exp_continue }
    "Password:"        { send "root\r"; exp_continue }
    "# "               {}
}

# ── Wait for peer VM ─────────────────────────────────────────────────────
send "/mnt/atp/scripts/qemu/atp-wait-peer.sh ${PROFILE}\r"
expect "# "

# ── Phase 1: all tests except stability ──────────────────────────────────
send "cd /mnt/atp && pytest --profile=${PROFILE} --ignore=tests/test_stability.py\r"
set timeout -1
expect "# "

# ── Countdown ────────────────────────────────────────────────────────────
# Output from puts goes to this terminal (not into the VM).
puts "\n--------------------------------------------------"
puts " Non-stability tests done."
puts " Stability tests will start in 10 seconds."
puts " Press Ctrl-a x to abort."
puts "--------------------------------------------------"
for {set i 10} {\$i >= 1} {incr i -1} {
    puts " \$i..."
    after 1000
}
puts " Starting stability tests now.\n"

# ── Phase 2: stability tests (handles reboots) ───────────────────────────
# rebooted==0: we are in the original session; "# " means tests finished.
# rebooted==1: we are in a fresh shell after a reboot; "# " means run the
#              stability continuation in the foreground and reset to 0.
send "cd /mnt/atp && pytest --profile=${PROFILE} tests/test_stability.py\r"
set rebooted 0
while 1 {
    expect {
        eof                { break }
        "buildroot login:" { set rebooted 1; send "root\r"; exp_continue }
        "Password:"        {                 send "root\r"; exp_continue }
        "# "               {
            if { \$rebooted == 0 } {
                send "poweroff\r"
                expect eof
                break
            }
            send "cd /mnt/atp && pytest --profile=${PROFILE} tests/test_stability.py\r"
            set rebooted 0
        }
    }
}
EXPECT_EOF

    expect -f "$EXPECT_SCRIPT"
else
    "$@"
fi
# trap cleanup (EXIT) runs here after QEMU exits
