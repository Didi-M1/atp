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
#   --port       TCP port of the DUT to connect to.
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
    IMG_FMT=$(qemu-img info --output=json "$ROOTFS_IMG" 2>/dev/null \
        | grep -o '"format"[[:space:]]*:[[:space:]]*"[^"]*"' \
        | sed 's/.*"\([^"]*\)"/\1/')
    IMG_FMT="${IMG_FMT:-raw}"
else
    MAGIC=$(dd if="$ROOTFS_IMG" bs=4 count=1 2>/dev/null | od -An -tx1 | tr -d ' \n')
    case "$MAGIC" in
        514669fb) IMG_FMT="qcow2" ;;
        *)         IMG_FMT="raw"  ;;
    esac
fi
echo "[qemu-peer] Rootfs: $(basename "$ROOTFS_IMG")  format=$IMG_FMT"
echo "[qemu-peer] Kernel: $(basename "$KERNEL")"

# ── Kernel cmdline ─────────────────────────────────────────────────────────────
KERNEL_APPEND="root=/dev/vda rw console=ttyS0,115200 net.ifnames=0 biosdevname=0 ip=192.168.200.2::192.168.200.254:255.255.255.0::eth0:off"

# ── Info ───────────────────────────────────────────────────────────────────────
echo ""
echo "---------------------------------------------------------------"
echo "  ATP Peer VM   RAM=512 MiB   CPU=$QEMU_CPU"
echo "  Peer IP:  192.168.200.2  (eth0)"
echo "  Connecting to DUT at 127.0.0.1:${SOCKET_PORT}"
echo "  (start qemu-run.sh first so the DUT is listening)"
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
# Network: connect to DUT socket (DUT must be listening first)
set -- "$@" -netdev "socket,id=net0,connect=127.0.0.1:${SOCKET_PORT}"
set -- "$@" -device "e1000,netdev=net0"
# Serial 0: guest console on stdio
set -- "$@" -serial mon:stdio
set -- "$@" -nographic
# Direct kernel boot (always used — required for ip= cmdline parameter)
set -- "$@" -kernel "$KERNEL"
set -- "$@" -append "$KERNEL_APPEND"

exec "$@"
# (trap cleanup runs after QEMU exits)
