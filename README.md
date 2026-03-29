# V4 Multi-Boot Selector

A firmware selector for the **Heltec WiFi LoRa 32 V4.2** (ESP32-S3) that lets you switch between multiple firmwares at boot time — Meshtastic, MeshCore, Reticulum/RNode, or anything else.

## How It Works

The 16MB flash is divided into a **factory partition** (boot selector app) and **4 OTA slots** that share a 12 MB region. By default each slot is 3 MB, but the installer wizard can resize them in 64 KB steps — e.g. 5 MB / 2 MB / 2 MB / 3 MB — to fit larger firmwares. On power-up, the selector shows a menu on the OLED labelled with each slot's `.bin` filename. Pick one with the USER button and the device reboots into it; the selection is remembered for auto-boot next time.

```
Flash Layout (16MB):
┌──────────────┬──────────┬────────────────────────────────┐
│ 0x000000     │  32KB    │ Bootloader                     │
│ 0x008000     │   4KB    │ Partition Table                │
│ 0x009000     │   4KB    │ Selector Config (sel_cfg)      │
│ 0x00E000     │   8KB    │ OTA Data (boot selection)      │
│ 0x010000     │   1MB    │ Boot Selector (factory)        │
│ 0x110000     │   12MB   │ OTA Slots 0-3 (configurable)   │
│ 0xD10000     │  544KB   │ SPIFFS (active)                │
│ 0xD98000     │ 4×544KB  │ Per-slot FS Backups            │
│ 0xFB8000     │  32KB    │ NVS (active)                   │
│ 0xFC0000     │ 4× 32KB  │ Per-slot NVS Backups           │
└──────────────┴──────────┴────────────────────────────────┘
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

The script defaults to `/dev/ttyACM1` and baud `115200` (required for the ESP32-S3's native USB-Serial-JTAG). Override with `-p` / `-b` if your device enumerates elsewhere.

### Interactive installer (recommended)

```bash
python3 scripts/flash_firmware.py install
```

Walks you through:

1. Picking a `.bin` from `firmware/` for each of the four slots (or leaving slots empty).
2. Reading each selected `.bin`, computing its app size, and **recommending** a per-slot size (firmware + ~25% headroom, rounded up to 64 KB). Any leftover 12 MB budget is added to the slot holding the largest firmware — the one most likely to grow on future updates. Empty slots are given the 64 KB minimum.
3. Sizing each slot — press Enter to accept the recommendation, or type `3`, `3M`, `2048K`, `0x300000`, or `auto` on one slot to absorb the remainder. Sum must be 12 MB and each size a multiple of 64 KB.
4. A diff vs the current `partitions.csv`.
5. Regenerating `partitions.csv`, running `pio run` to rebuild the selector, and flashing everything.

If the layout is unchanged, the rebuild step is skipped.

### Non-interactive provisioning

```bash
python3 scripts/flash_firmware.py flash-all \
    --slot0 firmware/meshtastic.bin \
    --slot1 firmware/meshcore.bin \
    --slot2 firmware/rnode.bin \
    --slot3 firmware/reticulum.bin
```

Flashes the bootloader, partition table, selector app, and all four firmware slots in one go.

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

- The four OTA slots share a **12 MB region**. Default layout is 4×3 MB, but the installer can reassign within that budget (e.g. one 5 MB slot for Meshtastic + three 2 MB slots). Each size must be a multiple of 64 KB.
- **Per-firmware data isolation**: Each firmware's NVS (settings, keys, IDs) and filesystem data are saved/restored automatically when switching. Meshtastic, MeshCore, RNode etc. each keep their own independent config.
- Built-in OTA updates within a firmware (e.g. Meshtastic's own OTA) won't work because the partition labels differ. Update via the flash script instead.
- The active SPIFFS partition is 544 KB and each slot has a matching 544 KB filesystem backup area plus a 32 KB NVS backup area — all fixed, regardless of how the 12 MB OTA region is carved up.
- The OLED menu labels each slot with the `.bin` filename the installer was given, not hardcoded firmware names.

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
