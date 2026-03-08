#!/bin/sh
# qemu-peer.sh — Launch the ATP back-to-back peer QEMU VM
#
# The peer VM sets up 192.168.200.2 on eth0 and responds to pings and packets
# sent by the DUT VM (192.168.200.1). It enables the network tests in ATP.
#
# Usage:
#   ./scripts/qemu/qemu-peer.sh ROOTFS_IMG [--kernel BZIMAGE]
#
# Requirements:
#   - Start the DUT VM first (qemu-run.sh) — it listens on TCP port 15555
#   - Both VMs reuse the same ROOTFS_IMG (the peer just needs a working network stack)
#
# The peer VM is intentionally minimal:
#   - 512 MiB RAM, 1 vCPU
#   - Single NIC: e1000, connects to DUT socket at 127.0.0.1:15555
#   - No 9p share, no second serial PTY
#   - Guest console on stdio (this terminal)
#
# Exit QEMU: press Ctrl-a x   (or Ctrl-a c for monitor, then "quit")

set -eu

# ── Arguments ──────────────────────────────────────────────────────────────────
ROOTFS_IMG="${1:-}"
KERNEL=""
shift 2>/dev/null || true

while [ $# -gt 0 ]; do
    case "$1" in
        --kernel) KERNEL="$2"; shift 2 ;;
        -h|--help) sed -n '2,/^[^#]/{ /^#/p }' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [ -z "$ROOTFS_IMG" ]; then
    echo "Usage: $0 ROOTFS_IMG [--kernel BZIMAGE]" >&2
    exit 1
fi
[ -f "$ROOTFS_IMG" ] || { echo "ERROR: image not found: $ROOTFS_IMG" >&2; exit 1; }

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
echo "[qemu-peer] Image: $(basename "$ROOTFS_IMG")  format=$IMG_FMT"

# ── Kernel cmdline ─────────────────────────────────────────────────────────────
KERNEL_APPEND="root=/dev/vda rw console=ttyS0,115200 net.ifnames=0 biosdevname=0 ip=192.168.200.2::192.168.200.254:255.255.255.0::eth0:off"

# ── Info ───────────────────────────────────────────────────────────────────────
echo ""
echo "---------------------------------------------------------------"
echo "  ATP Peer VM   RAM=512 MiB   CPU=$QEMU_CPU"
echo "  Peer IP:  192.168.200.2  (eth0)"
echo "  Connecting to DUT at 127.0.0.1:15555"
echo "  (start qemu-run.sh first so the DUT is listening)"
echo "  ttyS0 → this terminal (guest console)"
echo "---------------------------------------------------------------"
echo ""

# ── Build argument list ────────────────────────────────────────────────────────
set --
set -- "$@" qemu-system-x86_64
if [ -n "$KVM" ]; then
    set -- "$@" $KVM
fi
set -- "$@" -machine q35
set -- "$@" -cpu   "$QEMU_CPU"
set -- "$@" -m     512
set -- "$@" -smp   1
# Disk
set -- "$@" -drive "file=${ROOTFS_IMG},format=${IMG_FMT},if=virtio"
# Network: connect to DUT socket (DUT must be listening first)
set -- "$@" -netdev "socket,id=net0,connect=127.0.0.1:15555"
set -- "$@" -device "e1000,netdev=net0"
# Serial 0: guest console on stdio
set -- "$@" -serial mon:stdio
set -- "$@" -nographic
# Optional direct kernel boot
if [ -n "$KERNEL" ]; then
    set -- "$@" -kernel "$KERNEL"
    set -- "$@" -append "$KERNEL_APPEND"
fi

exec "$@"
