# QEMU Setup

Run ATP against a QEMU/KVM VM instead of real hardware. Useful for developing profiles and validating the framework itself.

## What you need

- `qemu-system-x86_64` with KVM support (falls back to software emulation if unavailable)
- A minimal Linux root filesystem image (`rootfs.img` — raw or qcow2)
- Optionally a kernel image (`bzImage`) for direct kernel boot

## Start the VMs

Start the DUT first (it listens for the peer on TCP port 15555), then the peer in a second terminal:

```bash
# Terminal 1 — DUT (Device Under Test)
./scripts/qemu/qemu-run.sh rootfs.img --kernel bzImage

# Terminal 2 — peer (needed for network tests)
./scripts/qemu/qemu-peer.sh rootfs.img --kernel bzImage
```

The `--kernel` flag is optional. Without it QEMU boots from the disk image directly.

## Network layout

```
DUT  192.168.200.1  (eth0)
Peer 192.168.200.2  (eth0)
     └── back-to-back via QEMU socket on localhost:15555
```

Networking is configured entirely via the kernel command line (`ip=...`) — no DHCP or init script needed.

## Run tests inside the DUT VM

```bash
# Mount the ATP project directory from the host (virtio-9p share)
mount -t 9p -o trans=virtio atp /mnt/atp

cd /mnt/atp
pytest --profile=example_qemu
```

## DUT VM hardware mapping

| QEMU device | Guest name | Profile key |
|---|---|---|
| virtio-blk | `/dev/vda` | `system.storage.devices` |
| e1000 NIC | `eth0` | `network.interface` |
| ICH9 EHCI root hub | (lsusb) | `peripherals.usb.expected_count` |
| acpitz thermal zone | — | `system.cpu.max_temp_c` |
| ttyS0 | console | (stdio, this terminal) |
| ttyS1 | — | `serial.ports[].device` — PTY path printed at startup |
| 9p share tag `atp` | `/mnt/atp` | custom_checks |

SPI, I2C, WiFi, Bluetooth, Audio, and GPU are not emulated — set them to `enabled: false` in your profile.

## Environment variables (DUT script)

| Variable | Default | Description |
|---|---|---|
| `QEMU_RAM` | `2048` | RAM in MiB |
| `QEMU_SMP` | `2` | vCPU count |
| `QEMU_CPU` | `host` (KVM) / `qemu64` (TCG) | CPU model |

## Kernel requirements

If you pass `--kernel`, the kernel must have these options for the 9p share to work:

```
CONFIG_NET_9P=y
CONFIG_NET_9P_VIRTIO=y
CONFIG_9P_FS=y
```

For reboot stability tests, also ensure:

```
CONFIG_MAGIC_SYSRQ=y
```

and pass `sysrq_always_enabled=1` on the kernel command line (already set by `qemu-run.sh`).

## Reboot stability in QEMU

Wire `scripts/atp-reboot-continue.sh` into the guest init system. The simplest approach for a minimal image:

```sh
# /etc/rc.local inside the VM
mount -t 9p -o trans=virtio atp /mnt/atp
/mnt/atp/scripts/atp-reboot-continue.sh &
```

The script exits immediately when no reboot test is in progress (safe to leave permanently).

## QEMU controls

| Key | Action |
|---|---|
| `Ctrl-a x` | Exit QEMU |
| `Ctrl-a c` | Switch to QEMU monitor |
| `quit` (in monitor) | Quit QEMU |
