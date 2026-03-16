#!/bin/sh
# qemu-run.sh — Launch the ATP Device Under Test (DUT) QEMU/KVM VM
#
# Usage:
#   ./scripts/qemu/qemu-run.sh [ROOTFS_IMG] [--kernel BZIMAGE] [--port PORT]
#
# Arguments:
#   ROOTFS_IMG   Root filesystem image (raw or qcow2).
#                Default: ../../buildroot/output/images/rootfs.ext2
#   --kernel     Kernel image for direct boot (sets eth0 IP via cmdline).
#                Default: ../../buildroot/output/images/bzImage
#   --port       TCP port the DUT listens on for the peer connection.
#                Default: 15555
#
# Environment variables:
#   QEMU_RAM   RAM in MiB         (default: 2048)
#   QEMU_CPU   CPU model          (default: "host" with KVM, "qemu64" without)
#   QEMU_SMP   vCPU count         (default: 2)
#
# The VM provides:
#   ttyS0   → this terminal (guest console + QEMU monitor via Ctrl-a c)
#   ttyS1   → PTY on host  (ATP serial test device — path printed at startup)
#   eth0    → 192.168.200.1, back-to-back socket to peer VM (port 15555)
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

# First positional arg may be the rootfs image; skip if it starts with '--'
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
echo "  Peer VM:  ./scripts/qemu/qemu-peer.sh  (start in a second terminal)"
echo "  Socket:   listening on TCP :${SOCKET_PORT}"
echo "  ttyS0  →  this terminal (guest console)"
echo "  ttyS1  →  PTY  (path printed by QEMU on the next line)"
echo "  9p tag:   atp → auto-mounted at /mnt/atp on boot"
echo "  Run tests: cd /mnt/atp && pytest --profile=example_qemu"
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
# Network: DUT listens; peer VM connects (qemu-peer.sh)
set -- "$@" -netdev "socket,id=net0,listen=:${SOCKET_PORT}"
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

exec "$@"
# (trap cleanup runs after QEMU exits)
