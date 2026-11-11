# HDMI bring-up — HP Envy x360 16-ad0013dx / Radeon 760M

**Status: investigation, not an HDMI driver.** No HDMI activation is claimed on
Sequoia; the present 1920×1200 internal display and guarded WindowServer service
must remain the known-good baseline.

## Verified machine and known architectural blockers

- HP 16-ad0013dx has HDMI 2.1 and two USB-C ports with DisplayPort 1.4a.
  This describes its Windows/firmware capability; macOS driver support is not implied.
- `src/phxaccel/accel/phxfb.cpp` exposes `getConnectionCount() == 1` and one
  firmware-initialized display mode. Its framebuffer is a scanout **already
  established by GOP**. It does **not** train HDMI, read HDMI EDID, or program
  an external DCN link.
- `src/phxaccel/accel/phxagdc.cpp` supplies exactly **one** framebuffer slot.
  The display pipe uses the internal panel's single scanout memory.
- `src/amd/rdna3_dcn314_scanout.cpp` contains general register-image functionality,
  but it is **not** proof of validated HP HDMI link programming.
- Metal compute and WindowServer on eDP are verified on this machine;
  external outputs have no equivalent validation.

**Do not** change `getConnectionCount()`, synthesize a second framebuffer,
write HDMI/DCN registers or enable HPD interrupts based only on a port label.
We need connector routing, EDID/HPD observations, and a guardable per-head
resource plan before programming the display engine.

## Stage 1: read-only connector discovery

Connect a known-working HDMI monitor and cable. Start with a monitor that can
accept **1920×1080 at 60 Hz**, even if it also supports higher modes. Verify it
works on another computer when possible.

From the GitHub `feature/hp760m-hdmi-discovery` checkout, with HDMI **unplugged**:

```bash
python3 tools/hdmi/hp_hdmi_probe.py --phase unplugged \
  --output "$HOME/Desktop/hp-hdmi-unplugged.json"
```

Plug HDMI in, wait for the monitor to settle, then run:

```bash
python3 tools/hdmi/hp_hdmi_probe.py --phase plugged \
  --compare "$HOME/Desktop/hp-hdmi-unplugged.json" \
  --output "$HOME/Desktop/hp-hdmi-plugged.json"
```

On macOS, these commands only query IOKit and System Profiler. The existing
single-panel driver may report **no change** even when HDMI HPD is electrically
asserted. That is a data point, not evidence of a bad HDMI cable.

On a **Linux live USB** with the open-source amdgpu driver, the same script
collects `/sys/class/drm/card*-HDMI-A-*` status, GPU PCI identity, available
modes and non-identifying EDID metadata. The unplugged/plugged comparison
should show a connector status transition. A live USB is optional but often
necessary to map actual physical HDMI connectors before implementing DCN/DMUB.

Neither mode of the probe writes to a driver, accesses DCN MMIO, performs
mode-setting, installs packages, clears failure markers or touches NVRAM.
It refuses to overwrite an output JSON file. It excludes raw EDID, full ioreg
and display serials from the collected reports.

If there are multiple GPUs, confirm that the `cardN-HDMI-A-X` entry belongs to
AMD PCI ID `1002:1900` and HP subsystem `103c:8c6a`. Linux numbering is dynamic:
never hardcode `card0` or connector `1` on the basis of one report.

## Stage 2: physical topology and safe link plan (not implemented)

Before a display write is considered:

1. Find the exact HDMI link encoder, DDC/I2C, HPD pin and DCN resource mapping
   from Linux's amdgpu DCN and the HP's validated ATOM/VBIOS table, using
   read-only measurements. Record which items are observed versus inferred.
2. Build a separate external-display plan with a hard hardware/OS gate.
   Preserve OTG0/eDP, live DMCUB settings, boot framebuffer and existing GPU
   queues exactly. Never use reference 780M addresses on the HP.
3. Establish bounded HDMI hot-plug detection and EDID parsing with no mode
   change; include unplug/replug tests and connector-identity checks.
4. Add a second independent framebuffer/display-pipe identity only after
   verified link resources and explicit opt-in. Validate the Sequoia IOKit ABI
   and AGDC framebuffers list on the target OS before enabling that path.
5. Limit the initial output experiment to **one 1080p60 SDR monitor**,
   with a known-good recovery EFI, single guarded next-boot authorization and
   failure fallback. Modesetting and HDMI link initialization are not safe
   live experiments on the working boot.

HDMI audio, high-refresh operation, HDR, HDMI 2.1 FRL, HDCP, USB-C DP alternate
mode, docking and hot-plug recovery are **separate later milestones**.

## Build/test milestones

- [x] Read-only macOS/Linux discovery probe added on development branch
- [x] Offline status-change and EDID-privacy tests written
- [ ] Collect real HDMI unplugged/plugged reports
- [ ] Identify confirmed HDMI encoder/DDC/HPD resources
- [ ] Test pure EDID/mode parser and link-plan fixtures
- [ ] Build opt-in no-output HPD monitoring
- [ ] Test isolated guarded HDMI 1080p60 output on actual HP
- [ ] Add independent external display to WindowServer

Do not publish working HDMI support in releases or merge any register-write
implementation until its real hardware validation and rollback behavior
are documented.
