#!/bin/sh
# qemu-peer.sh — Launch the ATP back-to-back peer QEMU VM
#
# Usage:
#   ./scripts/qemu/qemu-peer.sh [ROOTFS_IMG] [--kernel BZIMAGE] [--port PORT]
#
# Arguments:
#   ROOTFS_IMG   Root filesystem image (same image as the DUT).
#                Default: ../../buildroot/output/images/rootfs.ext2
#   --kernel     Kernel image for direct boot (sets eth0 IP via cmdline).
#                Default: ../../buildroot/output/images/bzImage
#   --port       UDP multicast port shared with the DUT VM.
#                Default: 15555
#
# The peer VM sets 192.168.200.2 on eth0 and responds to pings and packets
# sent by the DUT VM (192.168.200.1). It enables the network tests in ATP.
#
# Requirements:
#   - Start the DUT VM first (qemu-run.sh) — it listens on the TCP port
#   - Both VMs use the same ROOTFS_IMG
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

case "${ROOTFS_IMG}" in
    --*|"") ROOTFS_IMG="" ;;
    *)      shift 2>/dev/null || true ;;
esac

while [ $# -gt 0 ]; do
    case "$1" in
        --kernel) KERNEL="$2";      shift 2 ;;
        --port)   SOCKET_PORT="$2"; shift 2 ;;
        -h|--help) sed -n '2,/^[^#]/{ /^#/p }' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

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
    echo "[qemu-peer] KVM: enabled"
else
    KVM=""
    CPU_DEFAULT="qemu64"
    echo "[qemu-peer] KVM: not available — software emulation"
fi

QEMU_CPU="${QEMU_CPU:-$CPU_DEFAULT}"

# ── Image format ───────────────────────────────────────────────────────────────
if command -v qemu-img >/dev/null 2>&1; then
    IMG_FMT=$(qemu-img info "$ROOTFS_IMG" 2>/dev/null \
        | sed -n 's/^file format: //p')
fi
IMG_FMT="${IMG_FMT:-raw}"
echo "[qemu-peer] Rootfs: $(basename "$ROOTFS_IMG")  format=$IMG_FMT"
echo "[qemu-peer] Kernel: $(basename "$KERNEL")"

# ── Kernel cmdline ─────────────────────────────────────────────────────────────
KERNEL_APPEND="root=/dev/vda rw console=ttyS0,115200 net.ifnames=0 biosdevname=0 ip=192.168.200.2::192.168.200.254:255.255.255.0::eth0:off"

# ── Info ───────────────────────────────────────────────────────────────────────
echo ""
echo "---------------------------------------------------------------"
echo "  ATP Peer VM   RAM=512 MiB   CPU=$QEMU_CPU"
echo "  Peer IP:  192.168.200.2  (eth0)"
echo "  Network:  UDP multicast 230.0.0.1:${SOCKET_PORT} (shared with DUT)"
echo "  ttyS0 → this terminal (guest console)"
echo "---------------------------------------------------------------"
echo ""

# ── Temporary qcow2 overlay ────────────────────────────────────────────────────
# Create a per-run copy-on-write overlay so the raw rootfs is never write-locked.
# This lets the DUT and peer open the same backing file simultaneously.
OVERLAY=$(mktemp /tmp/atp-peer-XXXXXX.qcow2)
trap 'rm -f "$OVERLAY"' EXIT INT TERM
qemu-img create -f qcow2 -b "$ROOTFS_IMG" -F "$IMG_FMT" "$OVERLAY" >/dev/null
echo "[qemu-peer] Overlay: $OVERLAY"

# ── Build argument list ────────────────────────────────────────────────────────
set --
set -- "$@" qemu-system-x86_64
[ -n "$KVM" ] && set -- "$@" $KVM
set -- "$@" -machine q35
set -- "$@" -cpu   "$QEMU_CPU"
set -- "$@" -m     512
set -- "$@" -smp   1
# Disk: write to qcow2 overlay; backing file (rootfs.ext2) is opened read-only
set -- "$@" -drive "file=${OVERLAY},format=qcow2,if=virtio"
# Network: same UDP multicast group as the DUT — no connection, no race.
set -- "$@" -netdev "socket,id=net0,mcast=230.0.0.1:${SOCKET_PORT},localaddr=127.0.0.1"
set -- "$@" -device "e1000,netdev=net0"
# Serial 0: guest console on stdio
set -- "$@" -serial mon:stdio
set -- "$@" -nographic
# Direct kernel boot (always used — required for ip= cmdline parameter)
set -- "$@" -kernel "$KERNEL"
set -- "$@" -append "$KERNEL_APPEND"

# ── Launch ─────────────────────────────────────────────────────────────────────
if command -v expect >/dev/null 2>&1; then
    # Auto-login then hand control back to the user (interact).
    QEMU_WRAPPER=$(mktemp /tmp/atp-peer-XXXXXX.sh)
    EXPECT_SCRIPT=$(mktemp /tmp/atp-peer-expect-XXXXXX.tcl)
    trap 'rm -f "$OVERLAY" "$QEMU_WRAPPER" "$EXPECT_SCRIPT"' EXIT INT TERM
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
expect {
    "buildroot login:" { send "root\r"; exp_continue }
    "Password:"        { send "root\r"; exp_continue }
    "# "               {}
}
interact
EXPECT_EOF

    expect -f "$EXPECT_SCRIPT"
else
    echo "[qemu-peer] 'expect' not found — auto-login disabled. Install: sudo apt install expect"
    "$@"
fi
# trap cleanup (EXIT) runs here after QEMU exits
