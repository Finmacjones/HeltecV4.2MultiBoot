#!/usr/bin/env python3
"""
V4 Multi-Boot Flash Utility

Flash firmware binaries to specific OTA slots on the Heltec V4.2,
or manage the boot selector.

Usage:
    # Flash everything (selector + firmware bins)
    python3 flash_firmware.py --all --port /dev/ttyUSB0 \
        --selector .pio/build/selector/firmware.bin \
        --slot0 firmware/meshtastic.bin \
        --slot1 firmware/meshcore.bin \
        --slot2 firmware/reticulum.bin

    # Flash a single firmware to a slot
    python3 flash_firmware.py --slot 0 --firmware firmware/meshtastic.bin

    # Force return to boot selector menu
    python3 flash_firmware.py --menu

    # Show partition info
    python3 flash_firmware.py --info
"""

import argparse
import os
import struct
import subprocess
import sys
import tempfile

# Partition layout (must match partitions.csv and config.h)
PARTITIONS = {
    "bootloader": {"offset": 0x0000,    "size": 0x8000},
    "sel_cfg":    {"offset": 0x9000,    "size": 0x1000},     # 4KB - selector config
    "nvs":        {"offset": 0xA000,    "size": 0x4000},     # 16KB - selector NVS
    "otadata":    {"offset": 0xE000,    "size": 0x2000},
    "factory":    {"offset": 0x10000,   "size": 0x100000},   # 1MB - boot selector
    "ota_0":      {"offset": 0x110000,  "size": 0x300000},   # 3MB
    "ota_1":      {"offset": 0x410000,  "size": 0x300000},   # 3MB
    "ota_2":      {"offset": 0x710000,  "size": 0x300000},   # 3MB
    "ota_3":      {"offset": 0xA10000,  "size": 0x300000},   # 3MB
}

# Per-firmware data isolation areas (between OTA slots and end of flash)
#   0xD10000 - 0xD1FFFF : NVS backups (4 × 16KB = 64KB)
#   0xD20000 - 0xDAFFFF : Active SPIFFS (576KB, in partition table)
#   0xDB0000 - 0xE3FFFF : Slot 0 FS backup (576KB)
#   0xE40000 - 0xECFFFF : Slot 1 FS backup (576KB)
#   0xED0000 - 0xF5FFFF : Slot 2 FS backup (576KB)
#   0xF60000 - 0xFEFFFF : Slot 3 FS backup (576KB)
NVS_BACKUP_BASE  = 0xD10000
MAIN_NVS_SIZE    = 0x4000     # 16KB per slot
FS_PARTITION_OFF = 0xD20000
FS_PARTITION_SZ  = 0x90000    # 576KB
FS_BACKUP_BASE   = 0xDB0000

SLOT_NAMES = ["ota_0", "ota_1", "ota_2", "ota_3"]
MAX_FIRMWARE_SIZE = 0x300000  # 3MB

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


def validate_firmware(path, slot_index):
    """Check that a firmware file exists and fits in the slot."""
    if not os.path.exists(path):
        print(f"ERROR: Firmware file not found: {path}")
        return False

    size = os.path.getsize(path)

    # Account for merged images — only the app portion matters
    app_offset = detect_merged_image(path)
    effective_size = size - app_offset

    if effective_size > MAX_FIRMWARE_SIZE:
        print(f"ERROR: Firmware too large for slot {slot_index}:")
        print(f"  App size:  {effective_size:,} bytes ({effective_size / 1024 / 1024:.1f} MB)")
        print(f"  Max size:  {MAX_FIRMWARE_SIZE:,} bytes ({MAX_FIRMWARE_SIZE / 1024 / 1024:.1f} MB)")
        return False

    print(f"  Slot {slot_index}: {os.path.basename(path)} ({effective_size:,} bytes, "
          f"{effective_size * 100 // MAX_FIRMWARE_SIZE}% of slot)")
    return True


def run_esptool(esptool, port, baud, args):
    """Run esptool with given arguments."""
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

    try:
        run_esptool(esptool, args.port, args.baud, [
            "write_flash",
            hex(offset), flash_path,
        ])
    finally:
        if tmp_path:
            os.unlink(tmp_path)

    print(f"\nSlot {args.slot} flashed successfully at offset {hex(offset)}")


def cmd_flash_all(args):
    """Flash selector + all provided firmware binaries."""
    esptool = find_esptool()

    # Validate selector
    selector = args.selector
    if not os.path.exists(selector):
        print(f"ERROR: Selector binary not found: {selector}")
        print("  Build it first: pio run")
        sys.exit(1)

    # Build flash command with all components
    flash_args = ["write_flash"]

    # Bootloader
    if os.path.exists(BOOTLOADER_BIN):
        flash_args += ["0x0000", BOOTLOADER_BIN]
        print(f"  Bootloader: {BOOTLOADER_BIN}")
    else:
        print("WARNING: Bootloader binary not found, skipping")

    # Partition table
    if os.path.exists(PARTITIONS_BIN):
        flash_args += ["0x8000", PARTITIONS_BIN]
        print(f"  Partitions: {PARTITIONS_BIN}")
    else:
        print("WARNING: Partition table binary not found, skipping")

    # boot_app0.bin (OTA data init)
    if os.path.exists(BOOT_APP0_BIN):
        flash_args += [hex(PARTITIONS["otadata"]["offset"]), BOOT_APP0_BIN]
        print(f"  OTA data:   {BOOT_APP0_BIN}")

    # Selector app
    flash_args += [hex(PARTITIONS["factory"]["offset"]), selector]
    print(f"  Selector:   {selector}")

    # Firmware slots (detect and extract merged factory images)
    slot_args = [args.slot0, args.slot1, args.slot2, args.slot3]
    tmp_files = []
    for i, fw in enumerate(slot_args):
        if fw:
            if not validate_firmware(fw, i):
                sys.exit(1)
            flash_path, tmp_path = prepare_firmware(fw, i)
            if tmp_path:
                tmp_files.append(tmp_path)
            flash_args += [hex(PARTITIONS[SLOT_NAMES[i]]["offset"]), flash_path]

    try:
        run_esptool(esptool, args.port, args.baud, flash_args)
    finally:
        for tmp in tmp_files:
            os.unlink(tmp)
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

    print()
    print("Per-Firmware Data Isolation Areas")
    print("-" * 60)
    slot_names = ["Meshtastic", "MeshCore", "RNode", "Slot 4"]
    print("  NVS Backups:")
    for i in range(4):
        nvs_off = NVS_BACKUP_BASE + i * MAIN_NVS_SIZE
        print(f"    Slot {i} ({slot_names[i]}): {hex(nvs_off)} ({MAIN_NVS_SIZE // 1024}KB)")
    print(f"  Active SPIFFS: {hex(FS_PARTITION_OFF)} ({FS_PARTITION_SZ // 1024}KB)")
    print("  FS Backups:")
    for i in range(4):
        fs_off = FS_BACKUP_BASE + i * FS_PARTITION_SZ
        print(f"    Slot {i} ({slot_names[i]}): {hex(fs_off)} ({FS_PARTITION_SZ // 1024}KB)")

    total = 0x1000000  # 16MB
    print("-" * 60)
    print(f"Total flash: {total:,} bytes ({total // 1024 // 1024}MB)")


def main():
    parser = argparse.ArgumentParser(
        description="V4 Multi-Boot Flash Utility",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument("--port", "-p", default="/dev/ttyUSB0",
                        help="Serial port (default: /dev/ttyUSB0)")
    parser.add_argument("--baud", "-b", type=int, default=921600,
                        help="Baud rate (default: 921600)")

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

    args = parser.parse_args()

    if args.command == "flash-all":
        cmd_flash_all(args)
    elif args.command == "flash-slot":
        cmd_flash_slot(args)
    elif args.command == "menu":
        cmd_menu(args)
    elif args.command == "info":
        cmd_info(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
