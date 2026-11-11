#!/usr/bin/env python3
"""Offline tests for read-only HDMI connector inventory and EDID privacy."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SRC = Path(__file__).resolve().parents[1] / "tools/hdmi/hp_hdmi_probe.py"
spec = importlib.util.spec_from_file_location("hp_hdmi_probe", SRC)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def example_edid():
    data = bytearray(128)
    data[:8] = bytes.fromhex("00ffffffffffff00")
    # "DEL" encoded as three EISA 5-bit letters
    code = (4 << 10) | (5 << 5) | 12
    data[8:10] = code.to_bytes(2, "big")
    data[10:12] = (0x1234).to_bytes(2, "little")
    data[12:16] = b"\x12\x34\x56\x78"  # serial must never appear in report
    data[18:20] = b"\x01\x04"
    data[127] = -sum(data) & 255
    return bytes(data)


class HdmiProbeTest(unittest.TestCase):
    def test_edid_privacy(self):
        edid = probe.decode_edid(example_edid())
        self.assertTrue(edid["valid_header"])
        self.assertTrue(edid["block_checksums_valid"])
        self.assertEqual(edid["manufacturer"], "DEL")
        self.assertEqual(edid["product_code"], 0x1234)
        self.assertNotIn("serial", json.dumps(edid).lower())
        self.assertNotIn("78563412", json.dumps(edid))

    def test_linux_connector_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gpu = root / "card0/device"
            gpu.mkdir(parents=True)
            (gpu / "vendor").write_text("0x1002\n")
            (gpu / "device").write_text("0x1900\n")
            for name, status in (("card0-eDP-1", "connected"), ("card0-HDMI-A-1", "disconnected")):
                path = root / name
                path.mkdir()
                (path / "status").write_text(status + "\n")
                (path / "enabled").write_text("enabled\n" if status == "connected" else "disabled\n")
                (path / "modes").write_text("1920x1200\n")
                (path / "edid").write_bytes(example_edid() if status == "connected" else b"")
            before = probe.collect_linux(root)
            self.assertEqual(len(before["connectors"]), 2)
            before_hdmi = next(c for c in before["connectors"] if c["id"] == "card0-HDMI-A-1")
            self.assertEqual(before_hdmi["status"], "disconnected")
            (root / "card0-HDMI-A-1/status").write_text("connected\n")
            (root / "card0-HDMI-A-1/edid").write_bytes(example_edid())
            after = probe.collect_linux(root)
            diff = probe.compare(before, after)
            self.assertEqual(diff["status_changes"], [
                {"id": "card0-HDMI-A-1", "before": "disconnected", "after": "connected"}
            ])
            after_hdmi = next(c for c in after["connectors"] if c["id"] == "card0-HDMI-A-1")
            self.assertEqual(after_hdmi["gpu_device"], "0x1900")

    def test_macos_only_whitelisted_metadata(self):
        nodes = [{"IOClass": "PhxAMDFB", "PhxFBWidth": 1920,
                  "PhxFBHeight": 1200, "IORegistryEntryChildren": [
                      {"IOClass": "IODisplayConnect", "IODisplayEDID": example_edid(),
                       "IOPlatformSerialNumber": "SECRET"}]}]
        results = []
        probe.walk_ioreg(nodes, results)
        self.assertEqual(len(results), 2)
        self.assertNotIn("SECRET", json.dumps(results))
        self.assertEqual(results[1]["edid_present"], True)


if __name__ == "__main__":
    unittest.main()
