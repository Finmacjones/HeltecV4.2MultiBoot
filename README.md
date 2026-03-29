# V4 Multi-Boot Selector

A firmware selector for the **Heltec WiFi LoRa 32 V4.2** (ESP32-S3) that lets you switch between multiple firmwares at boot time — Meshtastic, MeshCore, Reticulum/RNode, or anything else.

## How It Works

The 16MB flash is divided into a **factory partition** (boot selector app) and **4 OTA slots** (3MB each). On power-up, the selector shows a menu on the OLED display. Pick a firmware with the USER button, and the device reboots into it. The selection is remembered for auto-boot next time.

```
Flash Layout (16MB):
┌──────────────┬──────────┬────────────────────────────┐
│ 0x000000     │ 32KB     │ Bootloader                 │
│ 0x008000     │  4KB     │ Partition Table             │
│ 0x009000     │  4KB     │ Selector Config             │
│ 0x00A000     │ 16KB     │ NVS (active)               │
│ 0x00E000     │  8KB     │ OTA Data (boot selection)  │
│ 0x010000     │  1MB     │ Boot Selector (factory)    │
│ 0x110000     │  3MB     │ Slot 0 - Meshtastic        │
│ 0x410000     │  3MB     │ Slot 1 - MeshCore          │
│ 0x710000     │  3MB     │ Slot 2 - RNode             │
│ 0xA10000     │  3MB     │ Slot 3 - (available)       │
│ 0xD10000     │  64KB    │ NVS Backups (4 × 16KB)     │
│ 0xD20000     │ 576KB    │ SPIFFS (active)            │
│ 0xDB0000     │ 576KB    │ Slot 0 FS Backup           │
│ 0xE40000     │ 576KB    │ Slot 1 FS Backup           │
│ 0xED0000     │ 576KB    │ Slot 2 FS Backup           │
│ 0xF60000     │ 576KB    │ Slot 3 FS Backup           │
└──────────────┴──────────┴────────────────────────────┘
```

## Requirements

- [PlatformIO](https://platformio.org/install/cli) (CLI or IDE)
- Heltec WiFi LoRa 32 V4.2
- USB-C cable
- Firmware `.bin` files for the firmwares you want to load

## Build

```bash
pio run
```

## Flash

### First time (selector + firmwares)

```bash
python3 scripts/flash_firmware.py --port /dev/ttyUSB0 flash-all \
    --slot0 firmware/meshtastic.bin \
    --slot1 firmware/meshcore.bin \
    --slot2 firmware/rnode.bin
```

This flashes the bootloader, partition table, selector app, and all firmware binaries in one go.

### Update a single slot

```bash
python3 scripts/flash_firmware.py flash-slot 0 firmware/meshtastic-new.bin
```

### Show partition info

```bash
python3 scripts/flash_firmware.py info
```

## Usage

1. **Press RST or power on** — the boot selector always appears
2. The splash screen appears for 1.5 seconds
3. If a firmware was previously selected, it **auto-boots after 5 seconds** with a countdown bar
4. **Short press USER** to cycle through firmware slots
5. **Long press USER** to boot the highlighted firmware
6. Press any button during countdown to cancel auto-boot and browse the menu

## Returning to the Boot Menu

**Just press RST.** The custom bootloader hook detects hardware resets and always boots the selector. This works because:

- **RST button / power cycle** → bootloader erases otadata → selector runs
- **Software restart** (from selector to firmware) → otadata preserved → firmware boots
- **Firmware crashes / watchdog** → selector runs (safe fallback)

To **skip auto-boot** and force the full menu: hold USER while pressing RST.

## Getting Firmware Binaries

| Firmware | Where to get .bin |
|----------|-------------------|
| Meshtastic | [meshtastic.org/downloads](https://meshtastic.org/downloads/) — pick `heltec-v3` variant |
| MeshCore | [flasher.meshcore.dev](https://flasher.meshcore.dev/) — pick Heltec V4 |
| Reticulum/RNode | Install `rnodeconf` and extract the firmware, or build from [source](https://github.com/markqvist/RNode_Firmware) |

## Notes

- Each firmware slot is 3MB. Firmwares larger than 3MB won't fit.
- **Per-firmware data isolation**: Each firmware's NVS (settings, keys, IDs) and filesystem data are saved/restored automatically when switching. Meshtastic, MeshCore, and RNode each keep their own independent config.
- Built-in OTA updates within a firmware (e.g., Meshtastic's own OTA) won't work because the partition labels differ. Update via the flash script instead.
- The active SPIFFS partition is 576KB. Each slot has a 576KB filesystem backup area and a 16KB NVS backup area.

## Project Structure

```
├── platformio.ini          # Build config
├── partitions.csv          # Flash partition table
├── boards/                 # Custom board definition
├── src/
│   ├── main.cpp            # Entry point
│   ├── config.h            # Pin defs, constants
│   ├── display.h/cpp       # OLED driver & UI rendering
│   ├── button.h/cpp        # USER button input
│   ├── firmware_manager.h/cpp  # Partition scanning & OTA
│   └── menu.h/cpp          # Menu state machine
├── scripts/
│   └── flash_firmware.py   # Flash utility
└── firmware/               # Put your .bin files here
```
