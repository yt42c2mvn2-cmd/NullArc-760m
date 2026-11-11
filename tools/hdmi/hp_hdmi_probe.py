#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Read-only HP 760M external-display discovery for macOS or Linux.

This does not open /dev/mem, read MMIO, set modes, load kexts or touch NVRAM.
Use with HDMI unplugged and plugged; Linux's amdgpu DRM connector inventory
provides the physical topology that the macOS boot-console framebuffer lacks.
The JSON intentionally excludes EDID serial numbers, raw EDID and full ioreg.
"""
import argparse
import datetime
import json
from pathlib import Path
import plistlib
import re
import subprocess
import sys

CONNECTOR = re.compile(r"^(card[0-9]+)-(eDP|HDMI-A|DP|DVI-I|DVI-D|VGA|LVDS)-([0-9]+)$")
SAFE_IOREG = ("IOClass", "IOName", "IONameMatched", "IOFBDependentIndex",
              "IOConnectionType", "IODisplayConnectFlags", "PhxFBWidth",
              "PhxFBHeight", "PhxFBHP", "PhxFBTimingCaptured")
SAFE_DISPLAYS = ("_name", "spdisplays_resolution", "spdisplays_pixels",
                 "spdisplays_main", "spdisplays_online",
                 "spdisplays_connection_type", "spdisplays_mirror")


def read_text(path, size=8192):
    try:
        return path.read_bytes()[:size].decode("utf-8", "replace").strip()
    except (OSError, ValueError):
        return None


def decode_edid(data):
    """Return only non-identifying EDID metadata; never disclose EDID serial."""
    if len(data) < 128 or data[:8] != bytes.fromhex("00ffffffffffff00"):
        return {"present": bool(data), "valid_header": False}
    blocks = len(data) // 128
    value = (data[8] << 8) | data[9]
    maker = "".join(chr(((value >> shift) & 31) + 64) for shift in (10, 5, 0))
    if not all("A" <= ch <= "Z" for ch in maker):
        maker = "unknown"
    return {
        "present": True,
        "valid_header": True,
        "blocks": blocks,
        "block_checksums_valid": all(sum(data[k*128:(k+1)*128]) % 256 == 0 for k in range(blocks)),
        "manufacturer": maker,
        "product_code": data[10] | (data[11] << 8),
        "edid_version": f"{data[18]}.{data[19]}",
        "extension_count_reported": data[126],
    }


def collect_linux(root=Path("/sys/class/drm")):
    """Read sysfs connector status/modes. No privileged DRM ioctls or writes."""
    result = []
    for path in sorted(root.glob("card*-*")):
        match = CONNECTOR.fullmatch(path.name)
        if not match:
            continue
        card, kind, index = match.groups()
        gpu = root / card / "device"
        try:
            edid = (path / "edid").read_bytes()[:4096]
        except OSError:
            edid = b""
        modes = (read_text(path / "modes") or "").splitlines()[:40]
        result.append({
            "id": path.name,
            "card": card,
            "type": kind,
            "number": int(index),
            "gpu_vendor": read_text(gpu / "vendor"),
            "gpu_device": read_text(gpu / "device"),
            "gpu_subsystem_vendor": read_text(gpu / "subsystem_vendor"),
            "gpu_subsystem_device": read_text(gpu / "subsystem_device"),
            "status": read_text(path / "status"),
            "enabled": read_text(path / "enabled"),
            "modes": modes,
            "edid": decode_edid(edid),
        })
    return {"connectors": result, "note": "Linux DRM connector data; no display settings changed"}


def safe_scalar(value):
    if isinstance(value, (bool, int)):
        return value
    if isinstance(value, str):
        return value[:120]
    return None


def walk_ioreg(node, found, depth=0):
    if depth > 10 or len(found) >= 150:
        return
    if isinstance(node, list):
        for child in node:
            walk_ioreg(child, found, depth + 1)
    elif isinstance(node, dict):
        cls = node.get("IOClass", "")
        if cls in ("IOFramebuffer", "PhxAMDFB", "IODisplayConnect") or "Framebuffer" in str(cls):
            item = {key: safe_scalar(node[key]) for key in SAFE_IOREG
                    if key in node and safe_scalar(node[key]) is not None}
            item["edid_present"] = "IODisplayEDID" in node
            if item:
                found.append(item)
        for key in ("IORegistryEntryChildren", "IOServiceChildren"):
            if key in node:
                walk_ioreg(node[key], found, depth + 1)


def run_json(command):
    try:
        result = subprocess.run(command, capture_output=True, check=True, timeout=20)
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def collect_macos():
    entries = []
    errors = []
    for cls in ("IOFramebuffer", "IODisplayConnect"):
        try:
            out = subprocess.run(["/usr/sbin/ioreg", "-a", "-r", "-c", cls],
                                 capture_output=True, check=True, timeout=20)
            walk_ioreg(plistlib.loads(out.stdout), entries)
        except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException) as exc:
            errors.append(f"{cls}: {type(exc).__name__}")
    raw = run_json(["/usr/sbin/system_profiler", "-json", "SPDisplaysDataType"])
    displays = []
    if isinstance(raw, dict):
        for gpu in raw.get("SPDisplaysDataType", []):
            for display in gpu.get("spdisplays_ndrvs", []):
                if isinstance(display, dict):
                    displays.append({key: safe_scalar(display[key]) for key in SAFE_DISPLAYS
                                     if key in display and safe_scalar(display[key]) is not None})
    return {"framebuffer_entries": entries, "system_displays": displays,
            "collection_warnings": errors,
            "note": "macOS registry and display inventory only; HDMI HPD pins may not be exported by the existing driver"}


def compare(old, new):
    """Compare Linux connector status and IDs; on macOS compare published display counts."""
    a = {c["id"]: c for c in old.get("connectors", [])}
    b = {c["id"]: c for c in new.get("connectors", [])}
    return {
        "added_connectors": sorted(set(b) - set(a)),
        "removed_connectors": sorted(set(a) - set(b)),
        "status_changes": [{"id": name, "before": a[name]["status"], "after": b[name]["status"]}
                           for name in sorted(set(a) & set(b))
                           if a[name].get("status") != b[name].get("status")],
        "mac_displays_before": len(old.get("system_displays", [])),
        "mac_displays_after": len(new.get("system_displays", [])),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("unplugged", "plugged"), required=True)
    parser.add_argument("--output", type=Path, help="new JSON path; refuses to overwrite")
    parser.add_argument("--compare", type=Path, help="compare with previous JSON snapshot")
    args = parser.parse_args()
    if sys.platform == "darwin":
        details = collect_macos()
    elif sys.platform.startswith("linux"):
        details = collect_linux()
    else:
        parser.error("only macOS and Linux are supported")
    report = {
        "schema": 1,
        "probe": "hp760m-hdmi-readonly",
        "phase": args.phase,
        "os": sys.platform,
        "time_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        **details,
    }
    if args.compare:
        previous = json.loads(args.compare.read_text())
        if previous.get("probe") != report["probe"] or previous.get("os") != report["os"]:
            parser.error("comparison must use a matching report from the same operating system")
        report["comparison"] = compare(previous, report)
    output = json.dumps(report, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as dest:
            dest.write(output)
        print(f"Saved read-only connector inventory: {args.output}")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
