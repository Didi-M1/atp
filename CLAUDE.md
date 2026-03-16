# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is ATP

ATP (Automated Test Platform) is a pytest-based hardware validation framework for embedded Linux boards. It uses YAML profiles to describe board hardware and validates CPU, RAM, storage, peripherals, firmware, network, serial ports, stability (reboot cycles), and stress performance.

## Common Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run all tests against a profile
pytest --profile=example_qemu

# Run a single test file or specific test
pytest --profile=example_qemu tests/test_system.py
pytest --profile=example_qemu tests/test_network.py::test_ping_latency

# Run tests by category (markers: system, peripherals, firmware, network, serial, stability, stress, manual)
pytest --profile=example_qemu -m network
pytest --profile=example_qemu -m "system or peripherals"

# Run operator-interactive tests (requires physical hardware and a human)
pytest --profile=my_board -m manual

# Exclude manual tests in CI / automated runs
pytest --profile=my_board -m "not manual"

# Use a custom profiles directory
pytest --profile=my_board --profiles-dir=/etc/atp/profiles

# Generate PDF report
pytest --profile=example_qemu --pdf=report.pdf

# Generate profile YAML from a running system
python scripts/atp-gen-profile.py
```

## QEMU Two-VM Workflow

```bash
# Terminal 1: DUT (Device Under Test)
./scripts/qemu/qemu-run.sh --profile example_qemu

# Terminal 2: Peer VM (required for network tests)
./scripts/qemu/qemu-peer.sh

# Inside DUT guest (manual run)
mount -t 9p -o trans=virtio atp /mnt/atp
cd /mnt/atp && pytest --profile=example_qemu
```

`qemu-run.sh --profile <name>` uses `expect` to auto-login and run tests non-interactively. The 9p virtio share (tag `atp`) exports the project root into the guest at `/mnt/atp`.

Default images: `../buildroot/output/images/bzImage` and `rootfs.ext2`. Override with `--kernel` and first positional arg. See `docs/buildroot-qemu.md` for building the Buildroot image.

## Architecture

### Profile System (`atp/profile.py`, `profiles/`)

Profiles are YAML files with inheritance (`extends: base`). `load_profile()` deep-merges child over parent — scalar/list values replace (lists do not append). Sections marked `enabled: "~REQUIRED~"` in `base.yaml` must be explicitly set in every platform profile or `ProfileIncompleteError` is raised before any tests run.

`base.yaml` is the master template documenting every possible key. `example_qemu.yaml` is the reference platform profile.

### Test Modules (`tests/`)

Each file maps to a pytest marker:

| File | Marker | What it validates |
|---|---|---|
| `test_system.py` | `system` | CPU, RAM, storage, RTC |
| `test_peripherals.py` | `peripherals` | USB, SPI, Ethernet, I2C, WiFi, BT, Audio, GPU |
| `test_firmware.py` | `firmware` | DMI/UEFI fields |
| `test_network.py` | `network` | Ping, latency, jitter, packet loss, MTU |
| `test_serial.py` | `serial` | Port existence, baud rate, loopback |
| `test_stability.py` | `stability` | Reboot sequence, poweroff |
| `test_stress.py` | `stress` | CPU/RAM/disk/GPU throughput |
| `test_manual.py` | `manual` | Operator-interactive: Ethernet link detect, cable ID, SD card, RTC battery |

All tests use the `profile` fixture (dict) from `conftest.py`. Tests skip via `pytest.skip()` when a profile value is `null` or a section is disabled.

### Test Ordering and Reboot Safety (`conftest.py`)

`pytest_collection_modifyitems` enforces two rules on every run:

1. **Stability tests always run last** — `test_stability.py` is moved to the end of the collected list so all other tests (network, stress, system, etc.) complete *before* the first reboot fires. The reboot-continuation script therefore only ever needs to resume stability; nothing else is left unrun.

2. **Skip already-passed tests after a reboot** — when `is_continuation()` is true (reboot state file exists), tests whose node IDs are in `~/.atp/session_state.json` are marked `skip`. This prevents duplication if the operator manually re-runs pytest during a reboot sequence.

`pytest_runtest_logreport` writes each passing test's node ID to `~/.atp/session_state.json` (fsync'd for reboot safety). `pytest_sessionstart` clears this file at the start of any fresh (non-continuation) run.

### Reboot State Machine (`atp/stability.py`)

`test_stability.py` triggers reboots via sysrq and the state machine persists progress in `~/.atp/reboot_state.json` (override: `ATP_STATE_FILE`). On each boot, `atp-reboot-continue.sh` (S50 init script on the guest) re-invokes pytest for the stability tests. When in QEMU expect mode (`--profile` flag), the `.atp-expect-mode` lock file suppresses this script so `expect` drives the loop instead.

### Manual Test Utilities (`atp/manual.py`)

`prompt_operator(*lines, title, confirm)` — writes a bordered instruction box directly to `/dev/tty` (bypasses pytest stdout capture) and blocks until operator presses ENTER. `wait_for_link(iface, target, timeout_s)` — polls `/sys/class/net/{iface}/operstate` with a live countdown, returns `(success, final_state)`.

### Shared Utilities (`atp/utils.py`)

`run_cmd(cmd, timeout)` → `(returncode, stdout, stderr)`, `read_file(path)`, `tool_exists(name)`, `glob_paths(pattern)`. Always check `tool_exists()` before calling optional tools like `ethtool`, `dmidecode`, `lsusb`.

### Report Generation (`atp/reporter.py`)

Registered as a pytest plugin when `--pdf` is given. Collects results via `pytest_runtest_makereport` hook and renders static HTML → PDF using weasyprint.

## Key Design Rules

- **Null = skip**: A `null` profile value means the test skips, not fails.
- **`assert_absent: true`**: Peripherals can be validated as absent (security hardening).
- **Lists replace, never append**: When inheriting profiles, list values from the child override the parent entirely.
- **`tool_exists()` before optional tools**: Tests must not fail because a tool is missing; they should skip.
- **State file paths**: `~/.atp/reboot_state.json` (active reboot sequence) and `~/.atp/session_state.json` (passed-test registry). Override the directory via `ATP_STATE_FILE`.
