# Building a QEMU Image for ATP with Buildroot

This document explains how to build the Linux image used to run ATP tests inside a QEMU virtual machine.

## Overview

The goal is to produce two files:

| File | Description |
|---|---|
| `output/images/bzImage` | Linux kernel (~6 MB) |
| `output/images/rootfs.ext2` | Root filesystem image (2 GB ext4) |

These are passed to `scripts/qemu/qemu-run.sh` via `--kernel` to boot the DUT VM.

---

## Directory structure (inside buildroot repo)

```
buildroot/
├── configs/
│   └── atp_qemu_x86_64_defconfig   ← main build configuration
└── board/
    └── atp/
        └── qemu-x86_64/
            ├── linux.config         ← kernel config
            └── post-build.sh        ← rootfs customisation script
```

---

## Step-by-step build

### 1. Load the ATP defconfig

```bash
cd ~/projects/buildroot
make atp_qemu_x86_64_defconfig
```

This writes `buildroot/.config` from `configs/atp_qemu_x86_64_defconfig`. It selects the architecture, kernel version, packages, and filesystem size.

### 2. Build everything

```bash
make -j$(nproc)
```

Buildroot will, in order:

1. Build the host toolchain (GCC cross-compiler for x86\_64)
2. Build the Linux kernel using `board/atp/qemu-x86_64/linux.config`
3. Build all selected packages (Python, pytest, ethtool, etc.)
4. Run `board/atp/qemu-x86_64/post-build.sh` to customise the rootfs
5. Pack everything into `output/images/rootfs.ext2`

The first build takes **30–45 minutes** (downloads + compiles GCC). Subsequent builds are much faster because downloaded sources are cached in `dl/`.

### 3. Verify the output

```bash
ls -lh output/images/
# bzImage       ~6 MB
# rootfs.ext2   ~2 GB
```

---

## What the defconfig sets up

**`configs/atp_qemu_x86_64_defconfig`**

```
BR2_x86_64=y                              # target architecture
BR2_SYSTEM_HOSTNAME="atp-dut"
BR2_TARGET_GENERIC_ROOT_PASSWD="root"     # root password

BR2_LINUX_KERNEL_CUSTOM_VERSION_VALUE="6.18.7"
BR2_LINUX_KERNEL_CUSTOM_CONFIG_FILE="board/atp/qemu-x86_64/linux.config"

BR2_TARGET_ROOTFS_EXT2_SIZE="2G"          # disk image size (must be >= profile min_size_gb)

# ATP dependencies
BR2_PACKAGE_PYTHON3=y
BR2_PACKAGE_PYTHON_PYTEST=y
BR2_PACKAGE_PYTHON_PYYAML=y               # required by ATP profile loader
BR2_ROOTFS_DEVICE_CREATION_DYNAMIC_EUDEV=y  # udev — required by usbutils
BR2_PACKAGE_USBUTILS=y                    # lsusb — USB peripheral test
BR2_PACKAGE_ETHTOOL=y                     # Ethernet speed/duplex test
BR2_PACKAGE_PCIUTILS=y                    # lspci
BR2_PACKAGE_DMIDECODE=y                   # DMI/BIOS firmware test
BR2_PACKAGE_IPROUTE2=y                    # ip link show — network test
BR2_PACKAGE_UTIL_LINUX=y
BR2_PACKAGE_UTIL_LINUX_BINARIES=y         # lsblk — storage test
```

> **Why eudev?** `usbutils` (lsusb) depends on libusb, which needs `/dev/bus/usb` device nodes. eudev creates them automatically at runtime. Without eudev, lsusb fails with exit code 1.

> **Why 2 GB image?** The ATP profile `example_qemu` requires `min_size_gb: 1`. The kernel reports slightly less than the raw image size, so a 512 MB image only shows ~0.5 GB to `lsblk`. 2 GB gives comfortable headroom.

---

## What the kernel config adds

**`board/atp/qemu-x86_64/linux.config`** is based on the upstream `board/qemu/x86_64/linux.config` with these additions:

| Option | Reason |
|---|---|
| `CONFIG_IP_PNP=y` | Kernel reads `ip=` from cmdline → sets eth0 IP before init runs |
| `CONFIG_NET_9P=y` | 9P network protocol (Plan 9 filesystem over network) |
| `CONFIG_NET_9P_VIRTIO=y` | Transport for virtio-9p (the host→guest share mechanism) |
| `CONFIG_9P_FS=y` | Filesystem driver so `mount -t 9p` works inside the guest |
| `CONFIG_E1000=y` | Intel e1000 NIC driver — QEMU uses e1000 for the back-to-back network |
| `CONFIG_MAGIC_SYSRQ=y` | Required for reboot stability test (kernel cmdline: `sysrq_always_enabled=1`) |
| `CONFIG_ACPI=y` / `CONFIG_ACPI_THERMAL=y` | ACPI subsystem for firmware/DMI tests and thermal zones |

---

## What post-build.sh does

**`board/atp/qemu-x86_64/post-build.sh`** runs after all packages are installed, before the image is packed. It:

1. Creates `/mnt/atp` — the mount point for the virtio-9p share
2. Creates `/var/lib/atp` — state directory used by the reboot stability test
3. Installs `/etc/init.d/S50atp` — a BusyBox init script that runs at every boot:
   - Mounts the host ATP project directory at `/mnt/atp` via virtio-9p
   - Runs `scripts/atp-reboot-continue.sh` in the background (exits immediately if no reboot test is in progress — safe to run always)

---

## A note on QEMU USB

QEMU's `q35` machine type does **not** enable USB by default. Without USB controllers, `lsusb` fails and `/dev/bus/usb` is never created.

The fix is in `scripts/qemu/qemu-run.sh` — four devices are added explicitly:

```sh
-device ich9-usb-ehci1,id=ehci1
-device ich9-usb-uhci1,id=uhci1,masterbus=ehci1.0,firstport=0
-device ich9-usb-uhci2,id=uhci2,masterbus=ehci1.0,firstport=2
-device ich9-usb-uhci3,id=uhci3,masterbus=ehci1.0,firstport=4
```

This adds one EHCI (USB 2.0) controller and three UHCI (USB 1.1) companion controllers, which is the standard ICH9 USB topology. The kernel's EHCI/UHCI drivers detect them via PCI and create the `/dev/bus/usb/` tree, making `lsusb` work.

---

## Rebuilding after changes

| What changed | Command |
|---|---|
| Only package list | `make atp_qemu_x86_64_defconfig && make` |
| Kernel config | `make linux-dirclean && make` |
| A single package | `make <pkg>-dirclean && make` |
| post-build.sh | `make rootfs-ext2` (skips package rebuild) |
| Full clean (keep downloads) | `make clean && make atp_qemu_x86_64_defconfig && make -j$(nproc)` |

Downloads are cached in `buildroot/dl/` and are never deleted by `make clean`.

---

## Logging in

After the VM boots:

```
login: root
password: root
```

The 9p share is mounted automatically by `S50atp`. You can run tests immediately:

```bash
cd /mnt/atp
pytest --profile=example_qemu
```
