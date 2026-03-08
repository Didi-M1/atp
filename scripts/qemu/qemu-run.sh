#!/bin/sh
# qemu-run.sh — Launch the ATP Device Under Test (DUT) QEMU/KVM VM
#
# Usage:
#   ./scripts/qemu/qemu-run.sh ROOTFS_IMG [--kernel BZIMAGE]
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
#   9p tag "atp" → ATP project directory (mount inside guest: see below)
#
# After the VM boots (guest commands):
#   mount -t 9p -o trans=virtio atp /mnt/atp
#   cd /mnt/atp && pytest --profile=example_qemu
#
# Kernel config required for 9p share:
#   CONFIG_NET_9P=y  CONFIG_NET_9P_VIRTIO=y  CONFIG_9P_FS=y
#
# Start the peer VM in a second terminal:
#   ./scripts/qemu-peer.sh ROOTFS_IMG [--kernel BZIMAGE]
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
    # Detect qcow2 by magic bytes: QFI\xfb = 0x514669fb
    MAGIC=$(dd if="$ROOTFS_IMG" bs=4 count=1 2>/dev/null | od -An -tx1 | tr -d ' \n')
    case "$MAGIC" in
        514669fb) IMG_FMT="qcow2" ;;
        *)         IMG_FMT="raw"  ;;
    esac
fi
echo "[qemu-run] Image: $(basename "$ROOTFS_IMG")  format=$IMG_FMT"

# ── ATP directory (virtio-9p share) ───────────────────────────────────────────
ATP_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# ── Kernel cmdline ─────────────────────────────────────────────────────────────
# Configures eth0 without needing DHCP or init scripts.
# ip= format: <client-ip>:<server-ip>:<gw-ip>:<netmask>:<hostname>:<device>:<autoconf>
KERNEL_APPEND="root=/dev/vda rw console=ttyS0,115200 net.ifnames=0 biosdevname=0 ip=192.168.200.1::192.168.200.254:255.255.255.0::eth0:off sysrq_always_enabled=1"

# ── Info ───────────────────────────────────────────────────────────────────────
echo ""
echo "---------------------------------------------------------------"
printf "  ATP DUT VM   RAM=%s MiB   CPU=%s   vCPUs=%s\n" \
    "$QEMU_RAM" "$QEMU_CPU" "$QEMU_SMP"
echo "  DUT IP:   192.168.200.1  (eth0)"
echo "  Peer VM:  ./scripts/qemu/qemu-peer.sh $ROOTFS_IMG"
echo "  ttyS0  →  this terminal (guest console)"
echo "  ttyS1  →  PTY  (path printed by QEMU on the next line)"
echo "  9p tag:   atp → mount -t 9p -o trans=virtio atp /mnt/atp"
echo "  Run tests: cd /mnt/atp && pytest --profile=example_qemu"
echo "---------------------------------------------------------------"
echo ""

# ── Build argument list ────────────────────────────────────────────────────────
# Using 'set --' to build an array in POSIX sh, preserving quoting for paths
# that might contain spaces.
set --
set -- "$@" qemu-system-x86_64
if [ -n "$KVM" ]; then
    set -- "$@" $KVM
fi
set -- "$@" -machine q35
set -- "$@" -cpu   "$QEMU_CPU"
set -- "$@" -m     "$QEMU_RAM"
set -- "$@" -smp   "$QEMU_SMP"
# Disk (virtio-blk → /dev/vda in guest)
set -- "$@" -drive "file=${ROOTFS_IMG},format=${IMG_FMT},if=virtio"
# Network: DUT listens; peer VM connects (qemu-peer.sh)
set -- "$@" -netdev "socket,id=net0,listen=:15555"
set -- "$@" -device "e1000,netdev=net0"
# virtio-9p: exports ATP directory as tag "atp"
set -- "$@" -virtfs "local,path=${ATP_DIR},mount_tag=atp,security_model=passthrough,id=atp0"
# Serial 0: guest console on stdio (with QEMU monitor multiplexed via Ctrl-a c)
set -- "$@" -serial mon:stdio
# Serial 1: PTY on host — QEMU prints the /dev/pts/X path at startup
set -- "$@" -serial pty
# Headless (no graphical display)
set -- "$@" -nographic
# Optional direct kernel boot
if [ -n "$KERNEL" ]; then
    set -- "$@" -kernel "$KERNEL"
    set -- "$@" -append "$KERNEL_APPEND"
fi

exec "$@"
