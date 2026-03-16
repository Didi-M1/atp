"""Peripheral detection tests: USB, SPI, Ethernet, I2C, WiFi, Bluetooth, Audio, GPU.

Profile keys used (under 'peripherals'):
    usb.enabled, usb.expected_count, usb.devices[].{vid, pid}
    spi.enabled, spi.buses[].device
    ethernet.enabled, ethernet.interfaces[].{name, speed_mbps}
    i2c.enabled, i2c.buses[].device
    wifi.enabled, wifi.interface, wifi.expected_ssid
    bluetooth.enabled, bluetooth.expected_controllers
    audio.enabled, audio.expected_playback_cards, audio.cards[].{name, description}
    gpu.enabled, gpu.vendor_id, gpu.render_node
"""
from __future__ import annotations

import pytest

from atp.utils import run_cmd, read_file, tool_exists, glob_paths

pytestmark = pytest.mark.peripherals


# ──────────────────────────────────────────────
# USB
# ──────────────────────────────────────────────

class TestUSB:

    @pytest.fixture(autouse=True)
    def _require(self, profile):
        if not profile.get("peripherals", {}).get("usb", {}).get("enabled", False):
            pytest.skip("USB tests disabled in profile")

    def test_lsusb_available(self):
        if not tool_exists("lsusb"):
            pytest.skip("lsusb not installed — add usbutils to packages.txt")

    def test_usb_minimum_count(self, profile):
        expected_count = (
            profile.get("peripherals", {}).get("usb", {}).get("expected_count")
        )
        if expected_count is None:
            pytest.skip("expected_count not set in profile")

        if not tool_exists("lsusb"):
            pytest.skip("lsusb not available")

        rc, out, _ = run_cmd("lsusb")
        assert rc == 0, "lsusb failed"
        count = len([l for l in out.splitlines() if l.strip()])
        assert count >= expected_count, (
            f"USB: expected >= {expected_count} devices, found {count}"
        )

    def test_usb_specific_devices(self, profile):
        devices = profile.get("peripherals", {}).get("usb", {}).get("devices", [])
        if not devices:
            pytest.skip("No specific USB devices defined in profile")

        if not tool_exists("lsusb"):
            pytest.skip("lsusb not available")

        rc, out, _ = run_cmd("lsusb")
        assert rc == 0, "lsusb failed"

        for dev in devices:
            vid = dev.get("vid", "")
            pid = dev.get("pid", "")
            desc = dev.get("description", f"{vid}:{pid}")
            needle = f"{vid}:{pid}".upper()
            found = any(needle in line.upper() for line in out.splitlines())
            assert found, f"USB device not found: {desc} ({vid}:{pid})"


# ──────────────────────────────────────────────
# SPI
# ──────────────────────────────────────────────

class TestSPI:

    @pytest.fixture(autouse=True)
    def _require(self, profile):
        if not profile.get("peripherals", {}).get("spi", {}).get("enabled", False):
            pytest.skip("SPI tests disabled in profile")

    def test_spi_bus_devices(self, profile):
        buses = profile.get("peripherals", {}).get("spi", {}).get("buses", [])
        if not buses:
            pytest.skip("No SPI buses defined in profile")

        import os
        for bus in buses:
            device = bus["device"]
            desc = bus.get("description", device)
            assert os.path.exists(device), (
                f"SPI device not found: {device} ({desc})"
            )


# ──────────────────────────────────────────────
# Ethernet
# ──────────────────────────────────────────────

class TestEthernet:

    @pytest.fixture(autouse=True)
    def _require(self, profile):
        if not profile.get("peripherals", {}).get("ethernet", {}).get("enabled", False):
            pytest.skip("Ethernet tests disabled in profile")

    def test_interfaces_exist(self, profile):
        ifaces = (
            profile.get("peripherals", {}).get("ethernet", {}).get("interfaces", [])
        )
        if not ifaces:
            pytest.skip("No Ethernet interfaces defined in profile")

        for iface in ifaces:
            name = iface["name"]
            rc, out, _ = run_cmd(f"ip link show {name}")
            assert rc == 0, f"Ethernet interface not found: {name}"

    def test_interfaces_have_link(self, profile):
        """Interfaces should be UP or at least present in the kernel."""
        ifaces = (
            profile.get("peripherals", {}).get("ethernet", {}).get("interfaces", [])
        )
        if not ifaces:
            pytest.skip("No Ethernet interfaces defined in profile")

        for iface in ifaces:
            name = iface["name"]
            rc, out, _ = run_cmd(f"ip link show {name}")
            if rc != 0:
                pytest.fail(f"Interface {name} not found")
            # "state UP" or "LOWER_UP" flag in flags field
            state_ok = "UP" in out
            assert state_ok, (
                f"Interface {name} exists but has no link — connect an Ethernet "
                f"cable to {name} and re-run.\n{out}"
            )

    def test_interface_speed(self, profile):
        ifaces = (
            profile.get("peripherals", {}).get("ethernet", {}).get("interfaces", [])
        )
        needs_speed = [i for i in ifaces if i.get("speed_mbps")]
        if not needs_speed:
            pytest.skip("No speed_mbps defined for any interface in profile")

        if not tool_exists("ethtool"):
            pytest.skip("ethtool not installed — add to packages.txt")

        for iface in needs_speed:
            name = iface["name"]
            expected_mbps = iface["speed_mbps"]
            rc, out, _ = run_cmd(f"ethtool {name}")
            if rc != 0:
                pytest.fail(
                    f"ethtool {name} failed — interface exists but may have no link. "
                    f"Connect an Ethernet cable to {name} and re-run."
                )

            for line in out.splitlines():
                if "speed:" in line.lower():
                    # e.g. "Speed: 1000Mb/s"
                    token = line.split(":", 1)[1].strip()
                    actual_mbps = int("".join(filter(str.isdigit, token)))
                    assert actual_mbps == expected_mbps, (
                        f"{name}: expected {expected_mbps} Mb/s, got {actual_mbps} Mb/s — "
                        f"check the cable and switch port speed."
                    )
                    break
            else:
                pytest.fail(
                    f"Could not read link speed for {name} — "
                    f"connect an Ethernet cable to {name} and re-run."
                )


# ──────────────────────────────────────────────
# I2C
# ──────────────────────────────────────────────

class TestI2C:

    @pytest.fixture(autouse=True)
    def _require(self, profile):
        if not profile.get("peripherals", {}).get("i2c", {}).get("enabled", False):
            pytest.skip("I2C tests disabled in profile")

    def test_i2c_bus_devices(self, profile):
        buses = profile.get("peripherals", {}).get("i2c", {}).get("buses", [])
        if not buses:
            pytest.skip("No I2C buses defined in profile")

        import os
        for bus in buses:
            device = bus["device"]
            desc = bus.get("description", device)
            assert os.path.exists(device), (
                f"I2C device not found: {device} ({desc})"
            )


# ──────────────────────────────────────────────
# WiFi
# ──────────────────────────────────────────────

class TestWiFi:

    @pytest.fixture(autouse=True)
    def _require(self, profile):
        if not profile.get("peripherals", {}).get("wifi", {}).get("enabled", False):
            pytest.skip("WiFi tests disabled in profile")

    def _iface(self, profile) -> str:
        iface = profile.get("peripherals", {}).get("wifi", {}).get("interface")
        if not iface:
            pytest.skip("wifi.interface not set in profile")
        return iface

    def test_wifi_interface_exists(self, profile):
        iface = self._iface(profile)
        rc, _, _ = run_cmd(f"ip link show {iface}")
        assert rc == 0, f"WiFi interface not found: {iface}"

    def test_wifi_interface_up(self, profile):
        iface = self._iface(profile)
        state = read_file(f"/sys/class/net/{iface}/operstate")
        if state is None:
            pytest.skip(f"Cannot read operstate for {iface}")
        assert state == "up", f"WiFi interface {iface} is not up (state: {state})"

    def test_wifi_connected(self, profile):
        expected_ssid = (
            profile.get("peripherals", {}).get("wifi", {}).get("expected_ssid")
        )
        if not expected_ssid:
            pytest.skip("expected_ssid not set in profile")

        iface = self._iface(profile)
        if not tool_exists("iw"):
            pytest.skip("iw not installed")

        rc, out, _ = run_cmd(f"iw dev {iface} link")
        assert rc == 0, f"iw dev {iface} link failed"
        assert "Not connected" not in out, f"WiFi interface {iface} is not connected"
        assert expected_ssid in out, (
            f"Expected SSID '{expected_ssid}' not found in iw output:\n{out}"
        )


# ──────────────────────────────────────────────
# WiFi — absence assertion
# ──────────────────────────────────────────────

class TestWiFiAbsent:
    """Assert that NO WiFi hardware is present on this machine.

    Enabled by setting peripherals.wifi.assert_absent: true in the profile.
    This is a hardening check — it actively fails if any WiFi capability is
    detected, rather than simply skipping.
    """

    @pytest.fixture(autouse=True)
    def _require(self, profile):
        if not profile.get("peripherals", {}).get("wifi", {}).get("assert_absent", False):
            pytest.skip("wifi assert_absent not enabled in profile")

    def test_no_ieee80211_interfaces(self):
        """Kernel wireless subsystem (/sys/class/ieee80211) must be empty."""
        import os
        path = "/sys/class/ieee80211"
        if not os.path.isdir(path):
            return  # subsystem not even loaded — definitely no WiFi
        entries = [e for e in os.listdir(path) if not e.startswith(".")]
        assert not entries, (
            f"WiFi hardware detected in {path}: {entries}"
        )

    def test_no_wireless_net_interfaces(self):
        """No network interface should have a wireless/ subdirectory."""
        import os
        net_base = "/sys/class/net"
        wireless_ifaces = [
            iface for iface in os.listdir(net_base)
            if os.path.isdir(os.path.join(net_base, iface, "wireless"))
        ]
        assert not wireless_ifaces, (
            f"Wireless network interface(s) found: {wireless_ifaces}"
        )

    def test_no_wifi_in_rfkill(self):
        """rfkill must not list any Wireless LAN device."""
        rc, out, _ = run_cmd("rfkill list")
        if rc != 0:
            pytest.skip("rfkill not available")
        wifi_lines = [l for l in out.splitlines() if "Wireless LAN" in l]
        assert not wifi_lines, (
            f"WiFi device(s) found in rfkill:\n" + "\n".join(wifi_lines)
        )


# ──────────────────────────────────────────────
# Bluetooth
# ──────────────────────────────────────────────

class TestBluetooth:

    @pytest.fixture(autouse=True)
    def _require(self, profile):
        if not profile.get("peripherals", {}).get("bluetooth", {}).get("enabled", False):
            pytest.skip("Bluetooth tests disabled in profile")

    def test_bluetooth_controller_exists(self, profile):
        import os
        expected = (
            profile.get("peripherals", {}).get("bluetooth", {})
            .get("expected_controllers", 1)
        )
        bt_path = "/sys/class/bluetooth"
        if not os.path.isdir(bt_path):
            pytest.fail("No Bluetooth subsystem found in /sys/class/bluetooth")

        controllers = [
            e for e in os.listdir(bt_path)
            if not e.startswith(".")
        ]
        assert len(controllers) >= expected, (
            f"Bluetooth: expected >= {expected} controller(s), found {len(controllers)}"
        )

    def test_bluetooth_powered(self, profile):
        if not tool_exists("hciconfig"):
            pytest.skip("hciconfig not installed")

        rc, out, _ = run_cmd("hciconfig")
        assert rc == 0, "hciconfig failed"
        assert "UP RUNNING" in out, (
            f"No Bluetooth controller in UP RUNNING state:\n{out}"
        )


# ──────────────────────────────────────────────
# Bluetooth — absence assertion
# ──────────────────────────────────────────────

class TestBluetoothAbsent:
    """Assert that NO Bluetooth hardware is present on this machine.

    Enabled by setting peripherals.bluetooth.assert_absent: true in the profile.
    This is a hardening check — it actively fails if any Bluetooth capability
    is detected, rather than simply skipping.
    """

    @pytest.fixture(autouse=True)
    def _require(self, profile):
        if not profile.get("peripherals", {}).get("bluetooth", {}).get("assert_absent", False):
            pytest.skip("bluetooth assert_absent not enabled in profile")

    def test_no_bluetooth_sysfs(self):
        """Kernel Bluetooth subsystem (/sys/class/bluetooth) must be empty."""
        import os
        path = "/sys/class/bluetooth"
        if not os.path.isdir(path):
            return  # subsystem not loaded — definitely no BT
        entries = [e for e in os.listdir(path) if not e.startswith(".")]
        assert not entries, (
            f"Bluetooth hardware detected in {path}: {entries}"
        )

    def test_no_bluetooth_in_rfkill(self):
        """rfkill must not list any Bluetooth device."""
        rc, out, _ = run_cmd("rfkill list")
        if rc != 0:
            pytest.skip("rfkill not available")
        bt_lines = [l for l in out.splitlines() if "Bluetooth" in l]
        assert not bt_lines, (
            f"Bluetooth device(s) found in rfkill:\n" + "\n".join(bt_lines)
        )

    def test_no_bluetooth_usb_device(self):
        """lsusb must not show any device with Bluetooth USB class (e0:01:01)."""
        if not tool_exists("lsusb"):
            pytest.skip("lsusb not installed")
        rc, out, _ = run_cmd("lsusb -v 2>/dev/null")
        if rc != 0:
            pytest.skip("lsusb -v failed")
        # Bluetooth devices advertise bDeviceClass=224 (0xe0), SubClass=1, Protocol=1
        # Search for the human-readable string that lsusb prints
        bt_entries = [l for l in out.splitlines() if "Bluetooth" in l]
        assert not bt_entries, (
            f"Bluetooth USB device(s) detected by lsusb:\n" + "\n".join(bt_entries)
        )


# ──────────────────────────────────────────────
# Audio
# ──────────────────────────────────────────────

class TestAudio:

    @pytest.fixture(autouse=True)
    def _require(self, profile):
        if not profile.get("peripherals", {}).get("audio", {}).get("enabled", False):
            pytest.skip("Audio tests disabled in profile")

    def test_alsa_tool_available(self):
        if not tool_exists("aplay"):
            pytest.skip("aplay not installed — add alsa-utils to packages.txt")

    def test_alsa_playback_cards(self, profile):
        expected = (
            profile.get("peripherals", {}).get("audio", {})
            .get("expected_playback_cards")
        )
        if expected is None:
            pytest.skip("expected_playback_cards not set in profile")

        if not tool_exists("aplay"):
            pytest.skip("aplay not available")

        rc, out, _ = run_cmd("aplay -l")
        assert rc == 0, "aplay -l failed"
        count = sum(1 for line in out.splitlines() if line.startswith("card "))
        assert count >= expected, (
            f"Audio: expected >= {expected} playback card(s), found {count}"
        )

    def test_audio_cards_present(self, profile):
        cards = profile.get("peripherals", {}).get("audio", {}).get("cards", [])
        if not cards:
            pytest.skip("No audio cards defined in profile")

        if not tool_exists("aplay"):
            pytest.skip("aplay not available")

        rc, out, _ = run_cmd("aplay -l")
        assert rc == 0, "aplay -l failed"

        for card in cards:
            name = card["name"]
            desc = card.get("description", name)
            found = any(name.lower() in line.lower() for line in out.splitlines())
            assert found, f"Audio card not found: {desc} (looking for '{name}' in aplay -l)"


# ──────────────────────────────────────────────
# GPU
# ──────────────────────────────────────────────

class TestGPU:

    @pytest.fixture(autouse=True)
    def _require(self, profile):
        if not profile.get("peripherals", {}).get("gpu", {}).get("enabled", False):
            pytest.skip("GPU tests disabled in profile")

    def test_render_node_exists(self, profile):
        import os
        render_node = (
            profile.get("peripherals", {}).get("gpu", {}).get("render_node")
        )
        if not render_node:
            pytest.skip("render_node not set in profile")
        assert os.path.exists(render_node), (
            f"GPU render node not found: {render_node}"
        )

    def test_gpu_vendor_id(self, profile):
        import os
        expected_vid = (
            profile.get("peripherals", {}).get("gpu", {}).get("vendor_id")
        )
        if not expected_vid:
            pytest.skip("vendor_id not set in profile")

        drm_base = "/sys/class/drm"
        if not os.path.isdir(drm_base):
            pytest.fail("No DRM subsystem found in /sys/class/drm")

        for entry in sorted(os.listdir(drm_base)):
            # top-level card entries (card0, card1, …) not connectors
            if not entry.startswith("card") or "-" in entry:
                continue
            vendor_path = os.path.join(drm_base, entry, "device", "vendor")
            vendor = read_file(vendor_path)
            if vendor and vendor.lower() == expected_vid.lower():
                return  # found a matching card

        pytest.fail(
            f"No GPU with vendor_id {expected_vid} found under {drm_base}"
        )
