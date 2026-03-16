# ATP — Automated Test Platform

pytest-based hardware validation for embedded Linux boards. Write a YAML profile describing your board, run `pytest --profile=my_board`, get pass/fail per test.

---

## How it works

Each test module reads its configuration from the active profile. If a value is `null` or a section is disabled, the test skips. If the board doesn't match the profile, the test fails with a clear message.

The profile is deep-merged on top of `base.yaml`, so you only write the keys you care about.

---

## Profiles

Profiles live in `profiles/`. Every profile extends `base.yaml`:

```yaml
name: my_board
extends: base
description: "ACME FooBoard Rev B"

system:
  cpu:
    min_cores: 4
    expected_vendor: "GenuineIntel"
    max_temp_c: 90
  ram:
    min_total_mb: 3800
  storage:
    devices:
      - path: /dev/mmcblk0
        min_size_gb: 8

peripherals:
  usb:
    enabled: true
    expected_count: 4
  spi:
    enabled: false
  ethernet:
    enabled: true
    interfaces:
      - name: eth0
        speed_mbps: 1000
  i2c:
    enabled: false
  wifi:
    enabled: false
    assert_absent: true    # actively fails if WiFi hardware is detected
  bluetooth:
    enabled: false
    assert_absent: true
  audio:
    enabled: false
  gpu:
    enabled: false

firmware:
  enabled: true
  dmi:
    enabled: true
    checks:
      sys_vendor: "ACME Corp"
      product_name: "FooBoard Rev B"
  uefi:
    enabled: true
    boot_order_first: hdd  # hdd | usb | pxe

custom_checks:
  - name: "RTC accessible"
    command: "hwclock --show"
    expected_returncode: 0

stability:
  reboot:
    enabled: true
    count: 5
    max_boot_time_s: 30
  poweroff:
    enabled: false

network:
  enabled: true
  interface: eth0
  peer_ip: 192.168.100.2
  ping_count: 100
  max_latency_ms: 1.0
  max_packet_loss_pct: 0.0

serial:
  enabled: true
  ports:
    - device: /dev/ttyS0
      baud: 115200
      loopback: false
```

### Required sections

Peripheral sections marked `enabled: "~REQUIRED~"` in `base.yaml` must be explicitly set in every profile. If you forget one, `load_profile()` raises an error before any test runs and lists exactly what's missing.

The valid values are `enabled: true`, `enabled: false`, or `assert_absent: true` (for hardening checks that actively fail if hardware is detected).

---

## Running tests

```bash
pip install -r requirements.txt

# All tests
pytest --profile=my_board

# One category
pytest --profile=my_board -m network
pytest --profile=my_board -m "system or peripherals"

# Custom profiles directory
pytest --profile=my_board --profiles-dir=/etc/atp/profiles

# HTML report
pytest --profile=my_board --html=report.html

# PDF report (requires weasyprint)
pytest --profile=my_board --pdf=report.pdf
```

Available markers: `system`, `peripherals`, `firmware`, `network`, `serial`, `stability`.

---

## Reboot stability tests

The reboot test reboots the board N times and checks that each boot completes within `max_boot_time_s`. State survives reboots via `~/.atp/reboot_state.json` (no root access required; override with `ATP_STATE_FILE`).

Wire `scripts/atp-reboot-continue.sh` into your init system so it runs on every boot — it re-invokes pytest automatically after each reboot and is a no-op when no test is in progress.

```bash
# /etc/rc.local
/path/to/atp/scripts/atp-reboot-continue.sh &

# BusyBox inittab
::once:/path/to/atp/scripts/atp-reboot-continue.sh
```

---

## QEMU

To run ATP against a QEMU VM instead of real hardware, see [`scripts/qemu/README.md`](scripts/qemu/README.md).
