#!/usr/bin/env python3
"""
V4 Multi-Boot Flash Utility

Flash firmware binaries to specific OTA slots on the Heltec V4.2,
or manage the boot selector.

Usage:
    # Interactive wizard: pick .bin + size per slot, regen partition table,
    # rebuild selector, and flash everything.
    python3 flash_firmware.py install

    # Flash everything (selector + up to 4 firmware bins) non-interactively
    python3 flash_firmware.py flash-all \
        --selector .pio/build/selector/firmware.bin \
        --slot0 firmware/meshtastic.bin \
        --slot1 firmware/meshcore.bin \
        --slot2 firmware/rnode.bin \
        --slot3 firmware/reticulum.bin

    # Flash a single firmware to a slot
    python3 flash_firmware.py flash-slot 0 firmware/meshtastic.bin

    # Force return to boot selector menu
    python3 flash_firmware.py menu

    # Show partition info
    python3 flash_firmware.py info

The partition table is read from partitions.csv at runtime; the wizard
rewrites it when slot sizes change. Each --slotN .bin filename is stored at
sel_cfg (0x9000) so the selector's OLED menu labels slots with the .bin
basename, not hardcoded names.
"""

import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

# Repo root derived from this script's location (scripts/flash_firmware.py).
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARTITIONS_CSV = os.path.join(REPO_ROOT, "partitions.csv")

# Partitions not in partitions.csv but needed by flashing (raw-flash regions
# and the ESP-IDF 0x8000 partition-table slot).
EXTRA_PARTITIONS = {
    "bootloader": {"offset": 0x0000, "size": 0x8000},
    "sel_cfg":    {"offset": 0x9000, "size": 0x1000},  # 4KB selector config sector
}


def load_partitions(csv_path=PARTITIONS_CSV):
    """Parse partitions.csv into {name: {offset, size}} and merge extras.

    partitions.csv is the source of truth for ota_N / factory / spiffs / nvs /
    otadata offsets. sel_cfg and bootloader aren't in the CSV (raw flash), so
    they're injected from EXTRA_PARTITIONS.

    Each row is registered under its name column AND its subtype (so rows like
    'app0, app, factory, ...' are findable as both 'app0' and 'factory').
    """
    result = dict(EXTRA_PARTITIONS)
    with open(csv_path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 5:
                continue
            name, _type, subtype, offset, size = parts[0], parts[1], parts[2], parts[3], parts[4]
            if not name or not offset or not size:
                continue
            entry = {"offset": int(offset, 0), "size": int(size, 0)}
            result[name] = entry
            if subtype and subtype != name:
                result.setdefault(subtype, entry)
    return result


PARTITIONS = load_partitions()

# Per-firmware data isolation areas (fixed — don't move with OTA slot resizing).
#   0xD10000 - 0xD97FFF : Active SPIFFS (544KB, in partition table)
#   0xD98000 - 0xE1FFFF : Slot 0 FS backup (544KB)
#   0xE20000 - 0xEA7FFF : Slot 1 FS backup (544KB)
#   0xEA8000 - 0xF2FFFF : Slot 2 FS backup (544KB)
#   0xF30000 - 0xFB7FFF : Slot 3 FS backup (544KB)
#   0xFB8000 - 0xFBFFFF : Active NVS (32KB, in partition table)
#   0xFC0000 - 0xFDFFFF : NVS backups (4 × 32KB = 128KB)
NVS_BACKUP_BASE  = 0xFC0000
MAIN_NVS_SIZE    = 0x8000     # 32KB per slot
FS_PARTITION_OFF = 0xD10000
FS_PARTITION_SZ  = 0x88000    # 544KB
FS_BACKUP_BASE   = 0xD98000

SLOT_NAMES = ["ota_0", "ota_1", "ota_2", "ota_3"]

# OTA region bounds (fixed by the surrounding flash map).
OTA_REGION_START = 0x110000
OTA_REGION_END   = 0xD10000   # exclusive; equals FS_PARTITION_OFF
OTA_REGION_TOTAL = OTA_REGION_END - OTA_REGION_START   # 12 MB
OTA_ALIGN        = 0x10000    # 64 KB (ESP-IDF OTA app alignment)


def slot_size(slot_index):
    """Size in bytes of ota_<slot_index> from the current partition table."""
    return PARTITIONS[SLOT_NAMES[slot_index]]["size"]


def write_partitions_csv(slot_sizes, csv_path=PARTITIONS_CSV):
    """Rewrite partitions.csv with new ota_N offsets/sizes, preserving
    the comment header and every non-ota row verbatim.

    slot_sizes is a 4-tuple of ints; must sum to OTA_REGION_TOTAL and each
    must be a positive multiple of OTA_ALIGN.
    """
    assert len(slot_sizes) == 4, "need 4 slot sizes"
    assert all(s > 0 and s % OTA_ALIGN == 0 for s in slot_sizes), \
        f"slot sizes must be positive multiples of {hex(OTA_ALIGN)}"
    assert sum(slot_sizes) == OTA_REGION_TOTAL, \
        f"slot sizes must sum to {hex(OTA_REGION_TOTAL)} (got {hex(sum(slot_sizes))})"

    with open(csv_path) as f:
        lines = f.readlines()

    # Compute new offsets: contiguous starting at OTA_REGION_START.
    offsets = []
    off = OTA_REGION_START
    for s in slot_sizes:
        offsets.append(off)
        off += s

    ota_row_re = re.compile(r'^\s*ota_([0-3])\s*,')
    new_lines = []
    for line in lines:
        m = ota_row_re.match(line)
        if m:
            i = int(m.group(1))
            # Preserve the trailing Flags column if present.
            parts = [p.strip() for p in line.rstrip('\n').split(',')]
            flags = parts[5] if len(parts) > 5 else ''
            new_lines.append(
                f"ota_{i},       app,  ota_{i},   "
                f"0x{offsets[i]:X},   0x{slot_sizes[i]:X},   {flags}\n"
            )
        else:
            new_lines.append(line)

    with open(csv_path, 'w') as f:
        f.writelines(new_lines)


# sel_cfg at 0x9000 (see src/config.h / src/firmware_manager.cpp).
# Layout must match sel_cfg_t: magic(4) + last_slot(1) + reserved(3) + 4*32 names.
SEL_CFG_MAGIC = 0xB0075E1C
SEL_CFG_SIZE = 0x1000  # 4KB sector
SEL_SLOT_NAME_LEN = 32
SEL_CFG_STRUCT_FMT = '<IB3x' + f'{SEL_SLOT_NAME_LEN}s' * 4  # 4+1+3+4*32 = 136 bytes


def slot_name_from_path(path):
    """Derive a short display name from a .bin file path."""
    base = os.path.basename(path)
    if base.lower().endswith('.bin'):
        base = base[:-4]
    return base[:SEL_SLOT_NAME_LEN - 1]


def build_sel_cfg_bin(slot_names, last_slot=0xFF):
    """Pack a sel_cfg_t record into a full 4KB sector image."""
    names_b = [(n or '').encode('utf-8', errors='replace')[:SEL_SLOT_NAME_LEN - 1]
               for n in slot_names]
    # struct with 32s pads with NULs and truncates — ensure NUL terminator.
    rec = struct.pack(SEL_CFG_STRUCT_FMT, SEL_CFG_MAGIC, last_slot & 0xFF,
                      names_b[0], names_b[1], names_b[2], names_b[3])
    # Pad to full sector with 0xFF (matches flash erased state).
    return rec + b'\xFF' * (SEL_CFG_SIZE - len(rec))


def read_sel_cfg(esptool, port, baud):
    """Read the sel_cfg sector from the device and unpack slot names + last_slot.

    Returns (ok, last_slot, [name0, name1, name2, name3]). ok is False when
    the read failed or the sector was uninitialized / had wrong magic — in
    that case last_slot is 0xFF and names are all ''. Callers doing a
    read-modify-write must treat ok=False as "no baseline to preserve".
    """
    empty = (False, 0xFF, [''] * 4)
    with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as tmp:
        tmp_path = tmp.name
    try:
        cmd = esptool + [
            "--chip", "esp32s3", "--port", port, "--baud", str(baud),
            "read_flash", hex(PARTITIONS["sel_cfg"]["offset"]),
            hex(struct.calcsize(SEL_CFG_STRUCT_FMT)), tmp_path,
        ]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            return empty
        with open(tmp_path, 'rb') as f:
            data = f.read(struct.calcsize(SEL_CFG_STRUCT_FMT))
        if len(data) < struct.calcsize(SEL_CFG_STRUCT_FMT):
            return empty
        magic, last_slot, n0, n1, n2, n3 = struct.unpack(SEL_CFG_STRUCT_FMT, data)
        if magic != SEL_CFG_MAGIC:
            return empty
        names = []
        for raw in (n0, n1, n2, n3):
            name = raw.split(b'\x00', 1)[0].decode('utf-8', errors='replace')
            # Filter stored 0xFF placeholder bytes
            if name and all(0x20 <= ord(c) <= 0x7E for c in name):
                names.append(name)
            else:
                names.append('')
        return True, last_slot, names
    finally:
        try: os.unlink(tmp_path)
        except OSError: pass

# PlatformIO build artifacts
PIO_BUILD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              ".pio", "build", "selector")
BOOTLOADER_BIN = os.path.join(PIO_BUILD_DIR, "bootloader.bin")
PARTITIONS_BIN = os.path.join(PIO_BUILD_DIR, "partitions.bin")
BOOT_APP0_BIN = os.path.join(
    os.path.expanduser("~"), ".platformio", "packages",
    "framework-arduinoespressif32", "tools", "partitions", "boot_app0.bin"
)


def find_esptool():
    """Find esptool.py in PlatformIO packages or PATH."""
    # Try PlatformIO package first
    pio_esptool = os.path.join(
        os.path.expanduser("~"), ".platformio", "packages",
        "tool-esptoolpy", "esptool.py"
    )
    if os.path.exists(pio_esptool):
        return [sys.executable, pio_esptool]

    # Try system PATH
    try:
        subprocess.run(["esptool.py", "--help"], capture_output=True, check=True)
        return ["esptool.py"]
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    print("ERROR: esptool.py not found. Install it or build the project first (pio run).")
    sys.exit(1)


def detect_merged_image(path):
    """Detect if a firmware binary is a merged factory image.

    Merged images contain bootloader + partition table + app starting at 0x0.
    Returns the byte offset of the actual app within the file (0 if plain app).
    """
    try:
        with open(path, 'rb') as f:
            data = f.read(0x10020)
    except IOError:
        return 0

    if len(data) < 0x10020:
        return 0

    # A merged image has: ESP image at 0x0, partition table at 0x8000, app at 0x10000
    if data[0] != 0xE9:
        return 0

    # Partition table magic is bytes [0xAA, 0x50] = 0x50AA in little-endian
    pt_magic = struct.unpack_from('<H', data, 0x8000)[0]
    if pt_magic != 0x50AA:
        return 0

    if data[0x10000] != 0xE9:
        return 0

    # Verify the app at 0x10000 targets ESP32-S3
    chip_id = struct.unpack_from('<H', data, 0x1000C)[0]
    if chip_id == 0x0009:
        return 0x10000

    return 0


def prepare_firmware(path, slot_index):
    """Prepare a firmware binary for flashing to an OTA slot.

    Detects merged factory images and extracts the app portion.
    Returns (usable_path, temp_file_or_None). Caller must delete temp file if set.
    """
    app_offset = detect_merged_image(path)

    if app_offset > 0:
        print(f"  Slot {slot_index}: {os.path.basename(path)} is a merged factory image")
        print(f"             Extracting app from offset 0x{app_offset:X}")

        with open(path, 'rb') as f:
            f.seek(app_offset)
            app_data = f.read()

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.bin',
                                          prefix=f'slot{slot_index}_app_')
        tmp.write(app_data)
        tmp.close()
        return tmp.name, tmp.name
    else:
        return path, None


def effective_firmware_size(path):
    """Size in bytes of the app portion of a .bin (strips merged-image prefix)."""
    size = os.path.getsize(path)
    return size - detect_merged_image(path)


def validate_firmware(path, slot_index):
    """Check that a firmware file exists and fits in its target slot."""
    if not os.path.exists(path):
        print(f"ERROR: Firmware file not found: {path}")
        return False

    eff = effective_firmware_size(path)
    cap = slot_size(slot_index)

    if eff > cap:
        print(f"ERROR: Firmware too large for slot {slot_index}:")
        print(f"  App size:  {eff:,} bytes ({eff / 1024 / 1024:.2f} MB)")
        print(f"  Slot size: {cap:,} bytes ({cap / 1024 / 1024:.2f} MB)")
        return False

    pct = eff * 100 // cap if cap else 0
    print(f"  Slot {slot_index}: {os.path.basename(path)} ({eff:,} bytes, {pct}% of slot)")
    return True


def run_esptool(esptool, port, baud, args):
    """Run esptool with given arguments.

    Stub mode at 115200 is used so large multi-MB slot writes succeed: the
    ESP32-S3 ROM (--no-stub path) chokes on large FLASH_BEGIN erase requests
    with 'Failed to enter Flash download mode (Operation or feature not
    supported)' once the erase exceeds a few hundred KB. The stub chunks
    erases sector-by-sector and avoids this. The 'stub handshake hangs'
    issue documented for this hardware is specific to higher upload bauds —
    at 115200 the stub uploads cleanly.
    """
    cmd = esptool + [
        "--chip", "esp32s3",
        "--port", port,
        "--baud", str(baud),
    ] + args

    print(f"\n> {' '.join(cmd)}\n")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"\nERROR: esptool failed with exit code {result.returncode}")
        sys.exit(1)


def cmd_flash_slot(args):
    """Flash a single firmware to an OTA slot."""
    esptool = find_esptool()

    if args.slot < 0 or args.slot > 3:
        print("ERROR: Slot must be 0-3")
        sys.exit(1)

    print(f"Flashing firmware to slot {args.slot}:")
    if not validate_firmware(args.firmware, args.slot):
        sys.exit(1)

    flash_path, tmp_path = prepare_firmware(args.firmware, args.slot)

    slot_name = SLOT_NAMES[args.slot]
    offset = PARTITIONS[slot_name]["offset"]

    # Read-modify-write sel_cfg so the display name for this slot updates.
    # If the read failed (ok=False) we have no baseline — start from all blanks
    # rather than risk clobbering a partially-read record. If the read
    # succeeded we preserve last_slot and the other three names verbatim.
    ok, last_slot, names = read_sel_cfg(esptool, args.port, args.baud)
    if not ok:
        print("  (no existing sel_cfg record found; other slot names will stay blank)")
        last_slot = 0xFF
    names[args.slot] = slot_name_from_path(args.firmware)
    sel_cfg_bin = build_sel_cfg_bin(names, last_slot=last_slot)
    with tempfile.NamedTemporaryFile(delete=False, suffix='.bin',
                                     prefix='sel_cfg_') as tmp:
        tmp.write(sel_cfg_bin)
        sel_cfg_path = tmp.name

    try:
        run_esptool(esptool, args.port, args.baud, [
            "write_flash",
            hex(offset), flash_path,
            hex(PARTITIONS["sel_cfg"]["offset"]), sel_cfg_path,
        ])
    finally:
        if tmp_path:
            os.unlink(tmp_path)
        try: os.unlink(sel_cfg_path)
        except OSError: pass

    print(f"\nSlot {args.slot} flashed ({names[args.slot]}) at offset {hex(offset)}")


def cmd_flash_all(args):
    """Flash selector + all provided firmware binaries.

    Split into one esptool invocation per partition group: boot stack, then
    each populated OTA slot, then sel_cfg. Each invocation is independent,
    so a failure mid-sequence gives a clear error pointing at the specific
    slot and the user can retry just that slot with `flash-slot N ...`.
    """
    esptool = find_esptool()

    # Validate selector
    selector = args.selector
    if not os.path.exists(selector):
        print(f"ERROR: Selector binary not found: {selector}")
        print("  Build it first: pio run")
        sys.exit(1)

    # ---- Invocation 1: boot stack (bootloader + PT + otadata + selector) ----
    boot_args = ["write_flash"]
    if os.path.exists(BOOTLOADER_BIN):
        boot_args += ["0x0000", BOOTLOADER_BIN]
        print(f"  Bootloader: {BOOTLOADER_BIN}")
    else:
        print("WARNING: Bootloader binary not found, skipping")

    if os.path.exists(PARTITIONS_BIN):
        boot_args += ["0x8000", PARTITIONS_BIN]
        print(f"  Partitions: {PARTITIONS_BIN}")
    else:
        print("WARNING: Partition table binary not found, skipping")

    if os.path.exists(BOOT_APP0_BIN):
        boot_args += [hex(PARTITIONS["otadata"]["offset"]), BOOT_APP0_BIN]
        print(f"  OTA data:   {BOOT_APP0_BIN}")

    boot_args += [hex(PARTITIONS["factory"]["offset"]), selector]
    print(f"  Selector:   {selector}")

    print("\n--- Flashing boot stack ---")
    run_esptool(esptool, args.port, args.baud, boot_args)

    # ---- Invocations 2..N: one per populated slot ----
    slot_args = [args.slot0, args.slot1, args.slot2, args.slot3]
    tmp_files = []
    slot_names = [''] * 4
    try:
        for i, fw in enumerate(slot_args):
            if not fw:
                continue
            if not validate_firmware(fw, i):
                sys.exit(1)
            flash_path, tmp_path = prepare_firmware(fw, i)
            if tmp_path:
                tmp_files.append(tmp_path)
            slot_names[i] = slot_name_from_path(fw)

            print(f"\n--- Flashing slot {i} ({slot_names[i]}) ---")
            run_esptool(esptool, args.port, args.baud, [
                "write_flash",
                hex(PARTITIONS[SLOT_NAMES[i]]["offset"]), flash_path,
            ])

        # ---- Final invocation: sel_cfg (menu labels) ----
        # last_slot=0xFF means "no last boot recorded" — the selector will
        # pre-select the first valid slot.
        sel_cfg_bin = build_sel_cfg_bin(slot_names, last_slot=0xFF)
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin',
                                         prefix='sel_cfg_') as tmp:
            tmp.write(sel_cfg_bin)
            sel_cfg_path = tmp.name
        tmp_files.append(sel_cfg_path)

        print(f"\n--- Flashing sel_cfg (slot names = {slot_names}) ---")
        run_esptool(esptool, args.port, args.baud, [
            "write_flash",
            hex(PARTITIONS["sel_cfg"]["offset"]), sel_cfg_path,
        ])
    finally:
        for tmp in tmp_files:
            try: os.unlink(tmp)
            except OSError: pass

    print("\nAll components flashed successfully!")


def cmd_menu(args):
    """Erase otadata to force boot into selector menu."""
    esptool = find_esptool()

    print("Erasing OTA data to force boot selector menu...")
    offset = PARTITIONS["otadata"]["offset"]
    size = PARTITIONS["otadata"]["size"]

    run_esptool(esptool, args.port, args.baud, [
        "erase_region",
        hex(offset), hex(size),
    ])

    print("\nOTA data erased. Device will boot into selector menu on next reset.")


def cmd_info(args):
    """Display partition layout info."""
    print("V4 Multi-Boot Partition Layout (16MB Flash)")
    print("=" * 60)
    print(f"{'Name':<16} {'Offset':<12} {'Size':<20} {'End':<12}")
    print("-" * 60)

    for name, info in PARTITIONS.items():
        end = info["offset"] + info["size"]
        size_str = f"{info['size']:,} ({info['size'] // 1024}KB)"
        print(f"{name:<16} {hex(info['offset']):<12} {size_str:<20} {hex(end):<12}")

    # Try to read sel_cfg off the device for actual per-slot labels. If the
    # device isn't connected or sel_cfg is uninitialized, fall back to "slot N".
    esptool = find_esptool()
    ok, _last_slot, names = read_sel_cfg(esptool, args.port, args.baud)
    slot_labels = [n if n else f"slot {i}" for i, n in enumerate(names)]
    if not ok:
        slot_labels = [f"slot {i}" for i in range(4)]

    print()
    print("Per-Firmware Data Isolation Areas")
    print("-" * 60)
    print("  NVS Backups:")
    for i in range(4):
        nvs_off = NVS_BACKUP_BASE + i * MAIN_NVS_SIZE
        print(f"    Slot {i} ({slot_labels[i]}): {hex(nvs_off)} ({MAIN_NVS_SIZE // 1024}KB)")
    print(f"  Active SPIFFS: {hex(FS_PARTITION_OFF)} ({FS_PARTITION_SZ // 1024}KB)")
    print("  FS Backups:")
    for i in range(4):
        fs_off = FS_BACKUP_BASE + i * FS_PARTITION_SZ
        print(f"    Slot {i} ({slot_labels[i]}): {hex(fs_off)} ({FS_PARTITION_SZ // 1024}KB)")

    total = 0x1000000  # 16MB
    print("-" * 60)
    print(f"Total flash: {total:,} bytes ({total // 1024 // 1024}MB)")


def parse_size(text):
    """Parse a size string into bytes.

    Accepts: "3" (MB), "3M", "3MB", "2048K", "0x300000" (bytes). Returns None
    if the input is not a valid size expression.
    """
    if text is None:
        return None
    s = text.strip().upper().replace(' ', '')
    if not s:
        return None
    if s.startswith('0X'):
        try:
            return int(s, 16)
        except ValueError:
            return None
    unit = None
    if s.endswith('MB'):
        s, unit = s[:-2], 'M'
    elif s.endswith('KB'):
        s, unit = s[:-2], 'K'
    elif s.endswith('M') or s.endswith('K'):
        unit, s = s[-1], s[:-1]
    try:
        val = int(s)
    except ValueError:
        return None
    if unit == 'M' or unit is None:
        return val * 1024 * 1024
    if unit == 'K':
        return val * 1024
    return None


def format_mb(n):
    """Pretty-print a byte count as MB with one decimal if needed."""
    mb = n / (1024 * 1024)
    if abs(mb - round(mb)) < 0.05:
        return f"{int(round(mb))} MB"
    return f"{mb:.1f} MB"


def list_firmware_bins():
    """Return sorted list of .bin paths in firmware/."""
    fw_dir = os.path.join(REPO_ROOT, "firmware")
    if not os.path.isdir(fw_dir):
        return []
    out = []
    for name in sorted(os.listdir(fw_dir)):
        if name.lower().endswith('.bin'):
            out.append(os.path.join(fw_dir, name))
    return out


def _ceil_align(n, align=None):
    a = align or OTA_ALIGN
    return ((n + a - 1) // a) * a


def build_recommended_layout(slot_paths, headroom=0.25):
    """Recommend per-slot sizes based on actual firmware sizes.

    Each populated slot gets ceil(fw_size * 1.25) rounded up to 64 KB.
    Empty slots get the 64 KB minimum. Any leftover budget is added to the
    slot with the largest firmware (most likely to grow in future updates).

    Returns (sizes, bonus_slot) where sizes is a list of 4 ints summing to
    OTA_REGION_TOTAL, and bonus_slot is the index that received the leftover
    (or None if the budget was spent exactly). Returns (None, None) if the
    selected firmwares won't fit even at minimum alignment.
    """
    fw_sizes = [effective_firmware_size(p) if p else 0 for p in slot_paths]

    # Minimum viable per slot: fw rounded up, or a single 64KB sector if empty.
    min_sizes = [max(_ceil_align(s), OTA_ALIGN) for s in fw_sizes]
    if sum(min_sizes) > OTA_REGION_TOTAL:
        return None, None

    # Preferred: firmware + headroom.
    target = [_ceil_align(int(s * (1 + headroom))) if s > 0 else OTA_ALIGN
              for s in fw_sizes]
    # If headroom would overflow, fall back to minimum sizes.
    if sum(target) > OTA_REGION_TOTAL:
        target = list(min_sizes)

    remainder = OTA_REGION_TOTAL - sum(target)
    bonus = None
    if remainder > 0:
        bonus = max(range(4), key=lambda i: fw_sizes[i])
        target[bonus] += remainder
    return target, bonus


def cmd_install(args):
    """Interactive installer wizard.

    Walks the user through firmware selection, per-slot sizing, confirms the
    new layout, regenerates partitions.csv + rebuilds the selector, then
    delegates to cmd_flash_all.
    """
    global PARTITIONS

    print("=== V4.2 MultiBoot Installer ===\n")
    print(f"Port: {args.port}   (override with -p)\n")

    # 1. Discover firmwares
    bins = list_firmware_bins()
    if not bins:
        print(f"ERROR: No .bin files found in {os.path.join(REPO_ROOT, 'firmware')}")
        sys.exit(1)

    print("Available firmwares in firmware/:")
    for i, path in enumerate(bins, 1):
        eff = effective_firmware_size(path)
        print(f"  [{i}] {os.path.basename(path):<40} {eff // 1024:>5} KB")
    print()

    # 2. Current layout
    current_sizes = [slot_size(i) for i in range(4)]
    print(f"Current layout: [{', '.join(format_mb(s) for s in current_sizes)}]\n")

    # 3. Slot firmware selection
    print("Pick firmware for each slot (number, or blank to leave empty):")
    slot_paths = [None] * 4
    for i in range(4):
        while True:
            raw = input(f"  Slot {i}: ").strip()
            if raw == '':
                slot_paths[i] = None
                break
            try:
                idx = int(raw)
                if 1 <= idx <= len(bins):
                    slot_paths[i] = bins[idx - 1]
                    break
            except ValueError:
                pass
            print(f"    Invalid choice. Enter 1-{len(bins)} or blank.")

    if not any(slot_paths):
        print("\nNo firmwares selected. Aborting.")
        sys.exit(1)

    # 4. Recommendations based on firmware sizes
    recommended, bonus_slot = build_recommended_layout(slot_paths)
    if recommended is None:
        total_min = sum(
            _ceil_align(effective_firmware_size(p)) if p else OTA_ALIGN
            for p in slot_paths
        )
        print(f"\nERROR: Selected firmwares total {format_mb(total_min)} minimum "
              f"but only {format_mb(OTA_REGION_TOTAL)} is available. Remove one.")
        sys.exit(1)

    print("\nRecommended sizes (firmware + ~25% headroom, "
          "remainder given to the largest firmware's slot):")
    for i in range(4):
        path = slot_paths[i]
        if path:
            note = f"fw {format_mb(effective_firmware_size(path))}"
        else:
            note = "empty"
        marker = " ← gets remainder" if i == bonus_slot else ""
        print(f"  Slot {i}: {format_mb(recommended[i]):>7}   ({note}){marker}")
    print()

    # 5. Slot sizing
    print("Configure slot sizes (sum must be 12 MB, 64 KB steps).")
    print("Press Enter to accept the recommended size. Type 'auto' on any one")
    print("slot to absorb the remainder, or enter a size like '3', '2048K',")
    print("'0x300000', etc.\n")

    new_sizes = [None] * 4
    auto_slot = None
    while True:
        new_sizes = [None] * 4
        auto_slot = None
        ok = True
        for i in range(4):
            fw_note = ""
            if slot_paths[i]:
                fw_need = effective_firmware_size(slot_paths[i])
                fw_note = f", fw needs {format_mb(fw_need)}"
            cur_note = ""
            if current_sizes[i] != recommended[i]:
                cur_note = f", current {format_mb(current_sizes[i])}"
            prompt = (f"  Slot {i} size "
                      f"[rec {format_mb(recommended[i])}{fw_note}{cur_note}]: ")
            raw = input(prompt).strip().lower()

            if raw == '':
                new_sizes[i] = recommended[i]
            elif raw == 'auto':
                if auto_slot is not None:
                    print("    ERROR: 'auto' can only be used on one slot.")
                    ok = False
                    break
                auto_slot = i
                new_sizes[i] = 0  # placeholder, computed after loop
            else:
                val = parse_size(raw)
                if val is None:
                    print("    ERROR: Could not parse size.")
                    ok = False
                    break
                if val <= 0 or val % OTA_ALIGN != 0:
                    print(f"    ERROR: Size must be a positive multiple of {OTA_ALIGN // 1024} KB.")
                    ok = False
                    break
                new_sizes[i] = val

        if not ok:
            print("  Restarting size entry.\n")
            continue

        if auto_slot is not None:
            used = sum(new_sizes[j] for j in range(4) if j != auto_slot)
            remainder = OTA_REGION_TOTAL - used
            if remainder <= 0 or remainder % OTA_ALIGN != 0:
                print(f"    ERROR: 'auto' slot would get {remainder} bytes (need > 0, "
                      f"multiple of {OTA_ALIGN // 1024} KB). Restarting.\n")
                continue
            new_sizes[auto_slot] = remainder
            print(f"    → Slot {auto_slot} = {format_mb(remainder)}")

        total = sum(new_sizes)
        if total != OTA_REGION_TOTAL:
            diff = OTA_REGION_TOTAL - total
            sign = '+' if diff > 0 else ''
            print(f"  ERROR: Sizes sum to {format_mb(total)} "
                  f"(need {format_mb(OTA_REGION_TOTAL)}, {sign}{format_mb(diff)}). Restarting.\n")
            continue

        # Per-slot capacity check
        overflow = False
        for i in range(4):
            if slot_paths[i]:
                fw_need = effective_firmware_size(slot_paths[i])
                if fw_need > new_sizes[i]:
                    print(f"  ERROR: Slot {i} size {format_mb(new_sizes[i])} < firmware "
                          f"{os.path.basename(slot_paths[i])} ({format_mb(fw_need)}). Restarting.\n")
                    overflow = True
                    break
        if overflow:
            continue

        break  # all good

    # 5. Summary
    print("\nProposed layout:")
    off = OTA_REGION_START
    offsets = []
    for i in range(4):
        offsets.append(off)
        name = os.path.basename(slot_paths[i]) if slot_paths[i] else "(empty)"
        fw_size = effective_firmware_size(slot_paths[i]) if slot_paths[i] else 0
        pct = (fw_size * 100 // new_sizes[i]) if new_sizes[i] else 0
        fw_str = f"{format_mb(fw_size)}, {pct}%" if slot_paths[i] else ""
        print(f"  ota_{i}  0x{off:06X}  {format_mb(new_sizes[i]):>7}   "
              f"{name:<36} {fw_str}")
        off += new_sizes[i]
    print(f"                        {format_mb(OTA_REGION_TOTAL):>7} total\n")

    # 6. Diff
    layout_changed = any(new_sizes[i] != current_sizes[i] for i in range(4))
    if layout_changed:
        print("Changes vs current partitions.csv:")
        cur_off = OTA_REGION_START
        for i in range(4):
            if new_sizes[i] != current_sizes[i] or offsets[i] != cur_off:
                note = ""
                if offsets[i] != cur_off:
                    note = f"  (offset 0x{cur_off:06X} → 0x{offsets[i]:06X})"
                print(f"  ota_{i}: {format_mb(current_sizes[i])} → "
                      f"{format_mb(new_sizes[i])}{note}")
            cur_off += current_sizes[i]
    else:
        print("Layout unchanged — will skip pio rebuild.")
    print()

    # 7. Confirm
    confirm = input("Proceed? [Y/n]: ").strip().lower()
    if confirm and confirm not in ('y', 'yes'):
        print("Aborted.")
        sys.exit(0)

    # 8. Regenerate + rebuild if layout changed
    if layout_changed:
        # Check pio exists before touching anything
        if shutil.which("pio") is None:
            print("ERROR: 'pio' (PlatformIO) not found on PATH. Install it and retry.")
            sys.exit(1)

        backup = PARTITIONS_CSV + ".bak"
        shutil.copy2(PARTITIONS_CSV, backup)
        print(f"Backed up partitions.csv → {backup}")

        try:
            write_partitions_csv(new_sizes)
            print("Wrote new partitions.csv")

            print("\n> pio run\n")
            r = subprocess.run(["pio", "run"], cwd=REPO_ROOT)
            if r.returncode != 0:
                raise RuntimeError(f"pio run failed (exit {r.returncode})")
        except Exception as e:
            print(f"\nERROR: {e}")
            print(f"Restoring partitions.csv from {backup}")
            shutil.copy2(backup, PARTITIONS_CSV)
            sys.exit(1)

        # Reload the in-process partition table so cmd_flash_all sees new offsets.
        PARTITIONS = load_partitions()

    # 9. Delegate to cmd_flash_all
    class _Args:
        pass
    fa = _Args()
    fa.port = args.port
    fa.baud = args.baud
    fa.selector = os.path.join(PIO_BUILD_DIR, "firmware.bin")
    fa.slot0 = slot_paths[0]
    fa.slot1 = slot_paths[1]
    fa.slot2 = slot_paths[2]
    fa.slot3 = slot_paths[3]
    cmd_flash_all(fa)


def main():
    parser = argparse.ArgumentParser(
        description="V4 Multi-Boot Flash Utility",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument("--port", "-p", default="/dev/ttyACM1",
                        help="Serial port (default: /dev/ttyACM1)")
    parser.add_argument("--baud", "-b", type=int, default=115200,
                        help="Baud rate (default: 115200 — required for ESP32-S3 USB-Serial-JTAG)")

    sub = parser.add_subparsers(dest="command")

    # flash-all
    p_all = sub.add_parser("flash-all", help="Flash selector + firmware binaries")
    p_all.add_argument("--selector", "-s",
                       default=os.path.join(PIO_BUILD_DIR, "firmware.bin"),
                       help="Path to selector .bin")
    p_all.add_argument("--slot0", help="Firmware .bin for slot 0")
    p_all.add_argument("--slot1", help="Firmware .bin for slot 1")
    p_all.add_argument("--slot2", help="Firmware .bin for slot 2")
    p_all.add_argument("--slot3", help="Firmware .bin for slot 3")

    # flash-slot
    p_slot = sub.add_parser("flash-slot", help="Flash firmware to a single slot")
    p_slot.add_argument("slot", type=int, choices=[0, 1, 2, 3],
                        help="Slot number (0-3)")
    p_slot.add_argument("firmware", help="Path to firmware .bin")

    # menu
    sub.add_parser("menu", help="Force boot into selector menu")

    # info
    sub.add_parser("info", help="Show partition layout")

    # install (interactive wizard)
    sub.add_parser("install", help="Interactive installer: pick bins + sizes, rebuild, flash")

    args = parser.parse_args()

    if args.command == "flash-all":
        cmd_flash_all(args)
    elif args.command == "flash-slot":
        cmd_flash_slot(args)
    elif args.command == "menu":
        cmd_menu(args)
    elif args.command == "info":
        cmd_info(args)
    elif args.command == "install":
        cmd_install(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
