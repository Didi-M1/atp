#!/usr/bin/env python3
## @file atp-gen-profile.py
#  @brief Interactive ATP profile generator.
#
#  Auto-detects hardware (CPU, RAM, storage, interfaces, DMI, audio, GPU, …)
#  using the same atp.utils helpers that the test suite uses, then asks
#  targeted questions for thresholds and settings it cannot infer.
#  The result is a ready-to-use ATP profile YAML saved under profiles/.
#
#  @par Usage
#  @code
#    python3 scripts/atp-gen-profile.py
#  @endcode

from __future__ import annotations

import re
import sys
from pathlib import Path

# Allow `from atp.utils import …` when run as a script from any directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
from atp.utils import glob_paths, read_file, run_cmd, tool_exists


# ── I/O helpers ───────────────────────────────────────────────────────────────

def ask(prompt: str, default=None, *, allow_empty: bool = False) -> str:
    """
    @brief Prompt the user for a string value.
    @param prompt    The question displayed to the user.
    @param default   Value accepted when the user presses Enter without typing.
    @param allow_empty  If True, an empty response is accepted (returns "").
    @return The user's input, or *default* if Enter was pressed.
    """
    hint = f" [{default}]" if default is not None else (" [skip]" if allow_empty else "")
    while True:
        raw = input(f"  {prompt}{hint}: ").strip()
        if raw == "" and default is not None:
            return str(default)
        if raw:
            return raw
        if allow_empty:
            return ""
        print("    (required — please enter a value)")


def ask_yn(prompt: str, default: bool = True) -> bool:
    """
    @brief Ask a yes/no question.
    @param prompt   The question displayed to the user.
    @param default  Answer used when the user presses Enter.
    @return True for yes, False for no.
    """
    hint = "Y/n" if default else "y/N"
    while True:
        raw = input(f"  {prompt} [{hint}]: ").strip().lower()
        if raw == "":
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("    Please enter y or n.")


def ask_int(prompt: str, default: int | None = None, *, min_val: int | None = None) -> int:
    """
    @brief Ask for an integer value.
    @param prompt   The question displayed to the user.
    @param default  Value accepted when the user presses Enter.
    @param min_val  If provided, reject values strictly below this floor.
    @return The integer entered by the user.
    """
    while True:
        raw = ask(prompt, default)
        try:
            val = int(raw)
            if min_val is not None and val < min_val:
                print(f"    Must be >= {min_val}.")
                continue
            return val
        except (ValueError, TypeError):
            print("    Please enter a whole number.")


def ask_float_or_none(prompt: str, default: float | None = None) -> float | None:
    """
    @brief Ask for a float; pressing Enter with no default skips (returns None).
    @param prompt   The question displayed to the user.
    @param default  Value accepted when the user presses Enter, or None to skip.
    @return The float entered by the user, or None if skipped.
    """
    hint = f" [{default}]" if default is not None else " [skip]"
    while True:
        raw = input(f"  {prompt}{hint}: ").strip()
        if raw == "":
            return default
        try:
            return float(raw)
        except ValueError:
            print("    Please enter a number (e.g. 85.0) or press Enter to skip.")


def section(title: str) -> None:
    """
    @brief Print a visual section header.
    @param title  The section name to display.
    """
    bar = "─" * 58
    print(f"\n{bar}\n  {title}\n{bar}")


def detected(msg: str) -> None:
    """
    @brief Print a hardware-detection status line.
    @param msg  The message to display, prefixed with an arrow.
    """
    print(f"  → {msg}")


# ── hardware probes (built on atp.utils) ─────────────────────────────────────

def probe_cpu() -> dict:
    """
    @brief Read CPU vendor, model name, and logical core count from procfs.
    @return Dict with keys: vendor (str|None), model (str|None), cores (int|None).
    """
    cpuinfo = read_file("/proc/cpuinfo") or ""
    vendor = model = None
    m = re.search(r"^vendor_id\s*:\s*(.+)$", cpuinfo, re.MULTILINE)
    if m:
        vendor = m.group(1).strip()
    m = re.search(r"^model name\s*:\s*(.+)$", cpuinfo, re.MULTILINE)
    if m:
        model = m.group(1).strip()
    cores = None
    if tool_exists("nproc"):
        _, out, _ = run_cmd("nproc")
        cores = int(out) if out.isdigit() else None
    return {"vendor": vendor, "model": model, "cores": cores}


def probe_ram_mb() -> int | None:
    """
    @brief Read total physical RAM from /proc/meminfo.
    @return RAM in megabytes, or None if unreadable.
    """
    meminfo = read_file("/proc/meminfo") or ""
    for line in meminfo.splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) // 1024
    return None


def probe_block_devices() -> list[dict]:
    """
    @brief List physical block devices with their sizes and guessed types.
    @return List of dicts, each with keys: path, size_gb (float|None), type (str|None).
    """
    if not tool_exists("lsblk"):
        return []
    _, out, _ = run_cmd("lsblk -dno NAME,SIZE,TYPE 2>/dev/null")
    devices = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[2] != "disk":
            continue
        name, size_str = parts[0], parts[1]
        gb = None
        m = re.match(r"([\d.]+)([TGMK])", size_str)
        if m:
            val, unit = float(m.group(1)), m.group(2)
            gb = val * {"T": 1024.0, "G": 1.0, "M": 1 / 1024, "K": 1 / 1048576}[unit]
        devtype = (
            "nvme"  if "nvme"   in name else
            "emmc"  if "mmcblk" in name else
            "ssd"   if name.startswith("sd") else
            None
        )
        devices.append({"path": f"/dev/{name}", "size_gb": gb, "type": devtype})
    return devices


def probe_usb_count() -> int | None:
    """
    @brief Count USB devices visible via lsusb.
    @return Device count, or None if lsusb is not available.
    """
    if not tool_exists("lsusb"):
        return None
    _, out, _ = run_cmd("lsusb 2>/dev/null")
    lines = [l for l in out.splitlines() if l.strip()]
    return len(lines) if lines else None


def probe_spi_devices() -> list[str]:
    """
    @brief Discover /dev/spidev* nodes.
    @return Sorted list of spidev device paths.
    """
    return sorted(glob_paths("/dev/spidev*"))


def probe_i2c_devices() -> list[str]:
    """
    @brief Discover /dev/i2c-* nodes.
    @return Sorted list of I2C device paths.
    """
    return sorted(glob_paths("/dev/i2c-*"))


def probe_net_interfaces() -> list[dict]:
    """
    @brief Enumerate non-loopback network interfaces.
    @return List of dicts with keys: name (str), wifi (bool), carrier (bool).
    """
    ifaces = []
    for entry in sorted(Path("/sys/class/net").iterdir()):
        if entry.name == "lo":
            continue
        is_wifi = (entry / "wireless").exists() or entry.name.startswith("wl")
        carrier = 0
        try:
            carrier = int((entry / "carrier").read_text().strip())
        except Exception:
            pass
        ifaces.append({"name": entry.name, "wifi": is_wifi, "carrier": bool(carrier)})
    return ifaces


def probe_bluetooth_count() -> int:
    """
    @brief Count Bluetooth controllers in /sys/class/bluetooth.
    @return Number of controllers found (0 if the directory does not exist).
    """
    return len(glob_paths("/sys/class/bluetooth/*"))


def probe_audio_cards() -> list[str]:
    """
    @brief List ALSA playback card names via aplay -l.
    @return List of card name strings (empty if aplay is unavailable).
    """
    if not tool_exists("aplay"):
        return []
    _, out, _ = run_cmd("aplay -l 2>/dev/null")
    cards: list[str] = []
    for line in out.splitlines():
        m = re.match(r"^card \d+: \S+ \[(.+)\]", line)
        if m and m.group(1) not in cards:
            cards.append(m.group(1))
    return cards


def probe_gpu() -> tuple[str | None, str | None]:
    """
    @brief Detect the primary GPU PCI vendor ID and DRI render node.
    @return Tuple of (vendor_id_hex_string | None, render_node_path | None).
    """
    vid = None
    if tool_exists("lspci"):
        _, out, _ = run_cmd("lspci -nn 2>/dev/null | grep -iE 'vga|3d|display'")
        for line in out.splitlines():
            m = re.search(r"\[([0-9a-fA-F]{4}):[0-9a-fA-F]{4}\]", line)
            if m:
                vid = f"0x{m.group(1).lower()}"
                break
    nodes = sorted(glob_paths("/dev/dri/renderD*"))
    return vid, (nodes[0] if nodes else None)


def probe_rtc() -> dict:
    """
    @brief Detect the first RTC device node and read the current hardware clock year.
    @return Dict with keys: device (str|None), year (int|None).
    """
    device = None
    for candidate in ["/dev/rtc0", "/dev/rtc"]:
        if Path(candidate).exists():
            device = candidate
            break
    year = None
    if tool_exists("hwclock"):
        _, out, _ = run_cmd("hwclock --show --utc 2>/dev/null")
        if out.strip():
            try:
                year = int(out.strip().split("-")[0])
            except (ValueError, IndexError):
                pass
    return {"device": device, "year": year}


def probe_serial_ports() -> list[str]:
    """
    @brief Discover serial port devices (ttyS*, ttyUSB*, ttyACM*).

    Physical ttyS ports are only included when a kernel driver is bound
    (i.e. /sys/class/tty/ttyS<N>/device exists), avoiding phantom entries.

    @return Sorted list of serial device paths that are actually present.
    """
    ports: list[str] = []
    for i in range(8):
        if Path(f"/sys/class/tty/ttyS{i}/device").exists():
            ports.append(f"/dev/ttyS{i}")
    ports += sorted(glob_paths("/dev/ttyUSB*"))
    ports += sorted(glob_paths("/dev/ttyACM*"))
    return ports


def probe_dmi() -> dict[str, str]:
    """
    @brief Read DMI / SMBIOS fields from /sys/class/dmi/id.
    @return Dict mapping DMI field names to their string values.
             Keys that cannot be read (permissions, absent) are omitted.
    """
    result: dict[str, str] = {}
    for key in ["sys_vendor", "product_name", "bios_vendor", "bios_version", "bios_date"]:
        val = read_file(f"/sys/class/dmi/id/{key}")
        if val:
            result[key] = val
    return result


# ── section configurators ─────────────────────────────────────────────────────

def cfg_system() -> dict:
    """
    @brief Interactively configure the 'system' profile section.
    @return Dict ready to be written as the 'system' key in the profile.
    """
    section("SYSTEM")

    cpu = probe_cpu()
    print(f"\n  CPU detected: {cpu['model']}  vendor={cpu['vendor']}  cores={cpu['cores']}")
    use_vendor = ask_yn("Assert CPU vendor_id?", default=True)
    use_model  = ask_yn("Assert CPU model name (substring match)?", default=True)
    min_cores  = ask_int("Minimum logical core count", default=cpu["cores"])
    max_temp   = ask_float_or_none("Max CPU temperature (°C) — Enter to skip")

    ram_mb = probe_ram_mb()
    detected(f"RAM: {ram_mb} MB")
    min_mb = ask_int("Minimum RAM (MB)", default=ram_mb)

    blk = probe_block_devices()
    detected(f"Block devices: {[d['path'] for d in blk] or 'none found'}")
    storage_devs = []
    for d in blk:
        label = f"{d['path']} ({d['size_gb']:.0f} GB, {d['type']})"
        if ask_yn(f"Include {label} in profile?", default=True):
            min_gb = ask_int(f"Min size for {d['path']} (GB)",
                             default=max(1, int((d["size_gb"] or 1) * 0.9)))
            dtype  = ask(f"Device type for {d['path']}", default=d["type"] or "ssd")
            storage_devs.append({"path": d["path"], "min_size_gb": min_gb, "type": dtype})

    rtc = probe_rtc()
    detected(f"RTC device: {rtc['device'] or 'not found'}  hwclock year: {rtc['year'] or 'n/a'}")
    rtc_enabled = ask_yn("Enable RTC checks?", default=bool(rtc["device"]))
    rtc_cfg: dict = {"enabled": rtc_enabled}
    if rtc_enabled:
        rtc_cfg["device"]    = ask("RTC device node", default=rtc["device"] or "/dev/rtc0")
        rtc_cfg["min_year"]  = ask_int("Minimum sane year for hwclock", default=rtc["year"] or 2020)
        rtc_cfg["max_drift_s"] = ask_float_or_none("Max drift vs system clock (s) — Enter to skip")

    return {
        "cpu": {
            "min_cores":       min_cores,
            "expected_vendor": cpu["vendor"] if use_vendor else None,
            "expected_model":  cpu["model"]  if use_model  else None,
            "max_temp_c":      max_temp,
        },
        "ram":     {"min_total_mb": min_mb},
        "storage": {"devices": storage_devs},
        "rtc":     rtc_cfg,
    }


def cfg_peripherals() -> dict:
    """
    @brief Interactively configure the 'peripherals' profile section.
    @return Dict ready to be written as the 'peripherals' key in the profile.
    """
    section("PERIPHERALS")
    p: dict = {}

    # ── USB ──
    print()
    usb_count = probe_usb_count()
    detected(f"USB: {usb_count} device(s) via lsusb" if usb_count is not None else "USB: lsusb not available")
    usb_enabled = ask_yn("Enable USB checks?", default=bool(usb_count))
    if usb_enabled:
        p["usb"] = {"enabled": True,
                    "expected_count": ask_int("Minimum USB device count", default=usb_count),
                    "devices": []}
    else:
        p["usb"] = {"enabled": False}

    # ── SPI ──
    print()
    spi_devs = probe_spi_devices()
    detected(f"SPI devices: {spi_devs or 'none'}")
    spi_enabled = ask_yn("Enable SPI checks?", default=bool(spi_devs))
    if spi_enabled:
        devs_str = ask("SPI devices (comma-separated)", default=",".join(spi_devs), allow_empty=True)
        buses = []
        for dev in [d.strip() for d in devs_str.split(",") if d.strip()]:
            entry: dict = {"device": dev}
            desc = ask(f"Description for {dev}", default="", allow_empty=True)
            if desc:
                entry["description"] = desc
            buses.append(entry)
        p["spi"] = {"enabled": True, "buses": buses}
    else:
        p["spi"] = {"enabled": False}

    # ── Ethernet ──
    print()
    all_ifaces      = probe_net_interfaces()
    eth_with_carrier = [i["name"] for i in all_ifaces if not i["wifi"] and i["carrier"]]
    detected(f"Ethernet (with carrier): {eth_with_carrier or 'none'}")
    eth_enabled = ask_yn("Enable Ethernet checks?", default=bool(eth_with_carrier))
    if eth_enabled:
        names_str = ask("Interfaces to check (comma-separated)",
                        default=",".join(eth_with_carrier), allow_empty=True)
        iface_list = []
        for name in [n.strip() for n in names_str.split(",") if n.strip()]:
            entry = {"name": name}
            speed = ask_int(f"Link speed for {name} (Mbps) — 0 to skip", default=1000)
            if speed:
                entry["speed_mbps"] = speed
            desc = ask(f"Description for {name}", default="", allow_empty=True)
            if desc:
                entry["description"] = desc
            iface_list.append(entry)
        p["ethernet"] = {"enabled": True, "interfaces": iface_list}
    else:
        p["ethernet"] = {"enabled": False}

    # ── I2C ──
    print()
    i2c_devs = probe_i2c_devices()
    detected(f"I2C devices: {i2c_devs or 'none'}")
    i2c_enabled = ask_yn("Enable I2C checks?", default=bool(i2c_devs))
    if i2c_enabled:
        devs_str = ask("I2C devices (comma-separated)", default=",".join(i2c_devs), allow_empty=True)
        buses = []
        for dev in [d.strip() for d in devs_str.split(",") if d.strip()]:
            entry = {"device": dev}
            desc = ask(f"Description for {dev}", default="", allow_empty=True)
            if desc:
                entry["description"] = desc
            buses.append(entry)
        p["i2c"] = {"enabled": True, "buses": buses}
    else:
        p["i2c"] = {"enabled": False}

    # ── WiFi ──
    print()
    wifi_ifaces = [i["name"] for i in all_ifaces if i["wifi"]]
    detected(f"WiFi interfaces: {wifi_ifaces or 'none'}")
    if wifi_ifaces:
        wifi_enabled = ask_yn("Enable WiFi checks?", default=True)
        if wifi_enabled:
            iface = ask("WiFi interface name", default=wifi_ifaces[0])
            ssid  = ask("Expected SSID — Enter to skip", default="", allow_empty=True) or None
            p["wifi"] = {"enabled": True, "assert_absent": False,
                         "interface": iface, "expected_ssid": ssid}
        else:
            p["wifi"] = {"enabled": False, "assert_absent": False}
    else:
        assert_no_wifi = ask_yn("Assert WiFi is absent (hardening check)?", default=False)
        p["wifi"] = {"enabled": False, "assert_absent": assert_no_wifi}

    # ── Bluetooth ──
    print()
    bt_count = probe_bluetooth_count()
    detected(f"Bluetooth controllers: {bt_count}")
    if bt_count:
        bt_enabled = ask_yn("Enable Bluetooth checks?", default=True)
        if bt_enabled:
            p["bluetooth"] = {"enabled": True, "assert_absent": False,
                               "expected_controllers": bt_count}
        else:
            p["bluetooth"] = {"enabled": False, "assert_absent": False}
    else:
        assert_no_bt = ask_yn("Assert Bluetooth is absent?", default=False)
        p["bluetooth"] = {"enabled": False, "assert_absent": assert_no_bt}

    # ── Audio ──
    print()
    audio_cards = probe_audio_cards()
    detected(f"ALSA playback cards: {audio_cards or 'none'}")
    audio_enabled = ask_yn("Enable audio checks?", default=bool(audio_cards))
    if audio_enabled:
        cards = []
        for card in audio_cards:
            if ask_yn(f"Assert card '{card}' present?", default=True):
                entry = {"name": card}
                desc = ask(f"Description for '{card}'", default="", allow_empty=True)
                if desc:
                    entry["description"] = desc
                cards.append(entry)
        p["audio"] = {"enabled": True, "expected_playback_cards": len(audio_cards), "cards": cards}
    else:
        p["audio"] = {"enabled": False}

    # ── GPU ──
    print()
    gpu_vid, render_node = probe_gpu()
    detected(f"GPU vendor_id: {gpu_vid or 'not found'}  render_node: {render_node or 'not found'}")
    gpu_enabled = ask_yn("Enable GPU checks?", default=bool(gpu_vid))
    if gpu_enabled:
        vid   = ask("GPU PCI vendor ID (e.g. 0x10de for NVIDIA, 0x8086 for Intel)", default=gpu_vid)
        rnode = ask("Render node path", default=render_node)
        p["gpu"] = {"enabled": True, "vendor_id": vid, "render_node": rnode}
    else:
        p["gpu"] = {"enabled": False}

    return p


def cfg_firmware() -> dict:
    """
    @brief Interactively configure the 'firmware' profile section (DMI + UEFI).
    @return Dict ready to be written as the 'firmware' key in the profile.
    """
    section("FIRMWARE / DMI / UEFI")

    dmi = probe_dmi()
    if dmi:
        print("\n  DMI values read from /sys/class/dmi/id/:")
        for k, v in dmi.items():
            detected(f"{k}: {v}")
    else:
        detected("Could not read DMI — /sys/class/dmi/id/ not accessible")

    fw_enabled = ask_yn("Enable firmware checks?", default=bool(dmi))
    if not fw_enabled:
        return {"enabled": False, "dmi": {"enabled": False}, "uefi": {"enabled": False}}

    dmi_enabled = ask_yn("Enable DMI field assertions?", default=bool(dmi))
    dmi_checks: dict = {}
    if dmi_enabled:
        for key, val in dmi.items():
            if ask_yn(f"Assert {key} = '{val}'?", default=True):
                dmi_checks[key] = val

    uefi_enabled = ask_yn("Enable UEFI checks?", default=False)
    uefi: dict = {"enabled": uefi_enabled}
    if uefi_enabled:
        boot_order    = ask("boot_order_first (hdd/usb/pxe/net)", default="", allow_empty=True) or None
        power_restore = ask("power_restore (last/on/off)",         default="", allow_empty=True) or None
        uefi["boot_order_first"] = boot_order
        uefi["power_restore"]    = power_restore

    return {
        "enabled": True,
        "dmi":  {"enabled": dmi_enabled, "checks": dmi_checks},
        "uefi": uefi,
    }


def cfg_custom_checks() -> list:
    """
    @brief Optionally collect arbitrary shell-command checks from the user.
    @return List of custom-check dicts (may be empty).
    """
    section("CUSTOM CHECKS")
    checks = []
    if not ask_yn("Add custom shell-command checks?", default=False):
        return checks
    while True:
        entry: dict = {
            "name":                ask("Check name"),
            "command":             ask("Shell command"),
            "expected_returncode": 0,
        }
        exp_out = ask("Expected output substring — Enter to skip", default="", allow_empty=True) or None
        if exp_out:
            entry["expected_output"] = exp_out
        checks.append(entry)
        if not ask_yn("Add another custom check?", default=False):
            break
    return checks


def cfg_stability() -> dict:
    """
    @brief Interactively configure the 'stability' profile section.
    @return Dict ready to be written as the 'stability' key in the profile.
    """
    section("STABILITY (REBOOT / POWEROFF)")

    reboot_enabled = ask_yn("Enable reboot test?", default=False)
    reboot: dict = {"enabled": reboot_enabled}
    if reboot_enabled:
        reboot["count"]      = ask_int("Number of reboots", default=3, min_val=1)
        reboot["timeout_s"]  = ask_int("Timeout per reboot (s)", default=120)
        max_boot = ask_float_or_none("Max allowed boot time (s) — Enter to skip")
        reboot["max_boot_time_s"] = int(max_boot) if max_boot is not None else None

    return {
        "reboot":  reboot,
        "poweroff": {"enabled": ask_yn("Enable poweroff test?", default=False)},
    }


def cfg_network() -> dict:
    """
    @brief Interactively configure the 'network' profile section.
    @return Dict ready to be written as the 'network' key in the profile.
    """
    section("NETWORK")

    all_ifaces       = probe_net_interfaces()
    eth_with_carrier = [i["name"] for i in all_ifaces if not i["wifi"] and i["carrier"]]
    detected(f"Ethernet with carrier: {eth_with_carrier or 'none'}")

    if not ask_yn("Enable network tests?", default=bool(eth_with_carrier)):
        return {"enabled": False}

    iface       = ask("Test interface", default=eth_with_carrier[0] if eth_with_carrier else "eth0")
    peer_ip     = ask("Peer IP address (back-to-back connected host)")
    mtu         = ask_int("Expected MTU", default=1500)
    jumbo       = ask_float_or_none("Jumbo MTU — Enter to skip")
    ping_count  = ask_int("Ping count", default=100)
    max_latency = ask_float_or_none("Max avg latency (ms)", default=10.0)
    max_jitter  = ask_float_or_none("Max jitter/mdev (ms) — Enter to skip")

    result: dict = {
        "enabled":              True,
        "interface":            iface,
        "peer_ip":              peer_ip,
        "mtu":                  mtu,
        "ping_count":           ping_count,
        "max_latency_ms":       max_latency,
        "max_packet_loss_pct":  0.0,
    }
    if jumbo      is not None: result["jumbo_mtu"]    = int(jumbo)
    if max_jitter is not None: result["max_jitter_ms"] = max_jitter
    return result


def cfg_serial() -> dict:
    """
    @brief Interactively configure the 'serial' profile section.
    @return Dict ready to be written as the 'serial' key in the profile.
    """
    section("SERIAL PORTS")

    detected_ports = probe_serial_ports()
    detected(f"Serial devices: {detected_ports or 'none'}")

    if not ask_yn("Enable serial tests?", default=bool(detected_ports)):
        return {"enabled": False}

    devs_str = ask("Devices to test (comma-separated)",
                   default=",".join(detected_ports), allow_empty=True)
    ports = []
    for dev in [d.strip() for d in devs_str.split(",") if d.strip()]:
        entry: dict = {
            "device":   dev,
            "baud":     ask_int(f"Baud rate for {dev}", default=115200),
            "loopback": ask_yn(f"Loopback plug installed on {dev}?", default=False),
        }
        desc = ask(f"Description for {dev}", default="", allow_empty=True)
        if desc:
            entry["description"] = desc
        ports.append(entry)
    return {"enabled": True, "ports": ports}


def cfg_stress() -> dict:
    """
    @brief Interactively configure the 'stress' profile section.
    @return Dict ready to be written as the 'stress' key in the profile.
    """
    section("STRESS TESTS")

    if not ask_yn("Enable stress tests?", default=False):
        return {"enabled": False}

    duration_s = ask_int("Stress duration per test (s)", default=30, min_val=1)

    # ── CPU ──
    print()
    cpu_info = probe_cpu()
    detected(f"CPU cores: {cpu_info['cores']}")
    cpu_enabled = ask_yn("Enable CPU stress test?", default=True)
    cpu: dict = {"enabled": cpu_enabled}
    if cpu_enabled:
        cpu["workers"] = ask_int("CPU worker count (0 = all cores)",
                                 default=cpu_info["cores"] or 0)
        min_mbps = ask_float_or_none("Min CPU throughput (MB/s) — Enter to skip")
        if min_mbps is not None:
            cpu["min_throughput_mbps"] = min_mbps

    # ── RAM ──
    print()
    ram_mb = probe_ram_mb()
    detected(f"Total RAM: {ram_mb} MB")
    ram_enabled = ask_yn("Enable RAM stress test?", default=True)
    ram: dict = {"enabled": ram_enabled}
    if ram_enabled:
        default_size = max(64, (ram_mb or 256) // 4)
        ram["size_mb"] = ask_int("RAM buffer size to test (MB)", default=default_size, min_val=1)
        min_write = ask_float_or_none("Min RAM write throughput (MB/s) — Enter to skip")
        min_read  = ask_float_or_none("Min RAM read  throughput (MB/s) — Enter to skip")
        if min_write is not None:
            ram["min_write_mbps"] = min_write
        if min_read is not None:
            ram["min_read_mbps"] = min_read

    # ── Disk ──
    print()
    blk = probe_block_devices()
    detected(f"Block devices: {[d['path'] for d in blk] or 'none found'}")
    disk_enabled = ask_yn("Enable disk stress test?", default=bool(blk))
    disk: dict = {"enabled": disk_enabled}
    if disk_enabled:
        disk["path"]    = ask("Disk path or directory for test file", default="/tmp")
        disk["size_mb"] = ask_int("Test file size (MB)", default=512, min_val=1)
        min_write = ask_float_or_none("Min disk write throughput (MB/s) — Enter to skip")
        min_read  = ask_float_or_none("Min disk read  throughput (MB/s) — Enter to skip")
        if min_write is not None:
            disk["min_write_mbps"] = min_write
        if min_read is not None:
            disk["min_read_mbps"] = min_read

    # ── GPU ──
    print()
    gpu_vid, render_node = probe_gpu()
    detected(f"GPU: vendor_id={gpu_vid or 'not found'}  render_node={render_node or 'not found'}")
    gpu_enabled = ask_yn("Enable GPU stress test (requires glmark2)?",
                         default=bool(render_node) and tool_exists("glmark2"))
    gpu: dict = {"enabled": gpu_enabled}
    if gpu_enabled:
        min_score = ask_float_or_none("Min glmark2 score — Enter to skip")
        if min_score is not None:
            gpu["min_score"] = int(min_score)

    return {
        "enabled":    True,
        "duration_s": duration_s,
        "cpu":        cpu,
        "ram":        ram,
        "disk":       disk,
        "gpu":        gpu,
    }


def cfg_manual() -> dict:
    """
    @brief Interactively configure the 'manual' profile section.
    @return Dict ready to be written as the 'manual' key in the profile.
    """
    section("MANUAL TESTS (operator-interactive)")
    print("  Manual tests require a human operator with physical access.")
    print("  Run with:  pytest --profile=<name> -m manual")
    print("  Skip in CI: pytest --profile=<name> -m \"not manual\"")

    if not ask_yn("Enable manual tests?", default=False):
        return {
            "enabled":     False,
            "ethernet":    {"link_test": [], "cable_id": []},
            "sd_card":     {"enabled": False},
            "rtc_battery": {"enabled": False},
        }

    # ── Ethernet link / cable-ID tests ───────────────────────────────────────
    print()
    all_ifaces = probe_net_interfaces()
    eth_names  = [i["name"] for i in all_ifaces if not i["wifi"]]
    detected(f"Ethernet interfaces: {eth_names or 'none'}")

    link_test: list[dict] = []
    cable_id:  list[dict] = []

    if eth_names:
        if ask_yn("Configure manual link-detect tests (connect / disconnect)?", default=False):
            print("  For each interface enter whether to include it in the link-detect test.")
            for name in eth_names:
                if ask_yn(f"  Test link detect on {name}?", default=True):
                    entry: dict = {"name": name}
                    desc = ask(f"    Description for {name}", default="", allow_empty=True)
                    if desc:
                        entry["description"] = desc
                    tmo = ask_int(f"    Link timeout for {name} (s)", default=15)
                    if tmo != 15:
                        entry["timeout_s"] = tmo
                    link_test.append(entry)

        if ask_yn("Configure cable-ID tests (verify which OS interface → which network)?",
                  default=False):
            print("  For each interface enter its network label (printed on the cable/switch).")
            for name in eth_names:
                lbl = ask(
                    f"  Network label for {name} (e.g. MANAGEMENT, DATA) — Enter to skip",
                    default="", allow_empty=True,
                ) or None
                if lbl:
                    entry = {"name": name, "network_label": lbl}
                    desc = ask(f"    Description for {name}", default="", allow_empty=True)
                    if desc:
                        entry["description"] = desc
                    cable_id.append(entry)

    # ── SD card ──
    print()
    sd_devs = sorted(glob_paths("/dev/mmcblk*"))
    detected(f"MMC/SD block devices: {sd_devs or 'none found'}")
    sd_enabled = ask_yn("Enable SD card test (insert/write/read/remove)?",
                        default=bool(sd_devs))
    sd: dict = {"enabled": sd_enabled}
    if sd_enabled:
        sd["device"] = ask("SD card block device", default=sd_devs[0] if sd_devs else "/dev/mmcblk0")
        min_size = ask_float_or_none("Minimum card size (MB) — Enter to skip")
        if min_size is not None:
            sd["min_size_mb"] = int(min_size)
        min_wr = ask_float_or_none("Minimum write throughput (MB/s) — Enter to skip")
        if min_wr is not None:
            sd["min_write_mbps"] = min_wr
        min_rd = ask_float_or_none("Minimum read throughput (MB/s) — Enter to skip")
        if min_rd is not None:
            sd["min_read_mbps"] = min_rd

    # ── RTC battery ──
    print()
    rtc = probe_rtc()
    detected(f"RTC device: {rtc['device'] or 'not found'}")
    rtc_enabled = ask_yn("Enable RTC battery power-cycle test?",
                         default=bool(rtc["device"]))
    rtc_bat: dict = {"enabled": rtc_enabled}
    if rtc_enabled:
        rtc_bat["max_drift_s"] = ask_int(
            "Maximum allowed RTC drift across power cycle (s)", default=5
        )

    return {
        "enabled":     True,
        "ethernet":    {"link_test": link_test, "cable_id": cable_id},
        "sd_card":     sd,
        "rtc_battery": rtc_bat,
    }


# ── YAML output ───────────────────────────────────────────────────────────────

def _none_representer(dumper, _):
    """
    @brief Custom PyYAML representer that serialises None as the literal 'null'.
    @param dumper  The YAML Dumper instance.
    @return A YAML scalar node for null.
    """
    return dumper.represent_scalar("tag:yaml.org,2002:null", "null")


yaml.add_representer(type(None), _none_representer)


def to_yaml(profile: dict) -> str:
    """
    @brief Serialise a profile dict to a YAML string with a header comment.
    @param profile  The fully-built profile dictionary.
    @return Multi-line YAML string ready to be written to disk.
    """
    header = (
        f"# ATP profile: {profile['name']}\n"
        "# Generated by atp-gen-profile.py\n"
        "# Review and adjust values before running tests.\n\n"
    )
    return header + yaml.dump(profile, default_flow_style=False,
                              allow_unicode=True, sort_keys=False)


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    """
    @brief Run the interactive profile-generation wizard.

    Walks through every profile section in order, auto-detects what it can,
    prompts the user for the rest, then writes the resulting YAML file.
    """
    print("┌─────────────────────────────────────────────────────────┐")
    print("│              ATP Profile Generator                      │")
    print("└─────────────────────────────────────────────────────────┘")
    print("Auto-detects hardware and asks targeted questions to build")
    print("a ready-to-use ATP profile YAML.\n")

    name        = ask("Profile name (used as filename)", default="my_board")
    description = ask("One-line description", default=f"Profile for {name}")

    profile: dict = {
        "name":        name,
        "extends":     "base",
        "description": description,
    }

    profile["system"]        = cfg_system()
    profile["peripherals"]   = cfg_peripherals()
    profile["firmware"]      = cfg_firmware()
    profile["custom_checks"] = cfg_custom_checks()
    profile["stability"]     = cfg_stability()
    profile["network"]       = cfg_network()
    profile["serial"]        = cfg_serial()
    profile["stress"]        = cfg_stress()
    profile["manual"]        = cfg_manual()

    section("SAVE PROFILE")
    profiles_dir = Path(__file__).resolve().parent.parent / "profiles"
    out_path = Path(ask("Output file path", default=str(profiles_dir / f"{name}.yaml")))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(to_yaml(profile))

    print(f"\n  Profile written to: {out_path}")
    print(f"\n  Run tests with:")
    print(f"    pytest --profile={name}\n")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\n\nAborted.")
        sys.exit(1)
