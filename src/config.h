#pragma once

// --- Heltec V4.2 Pin Definitions ---

// OLED Display (SSD1315, I2C)
#define OLED_SDA        17
#define OLED_SCL        18
#define OLED_RST        21
#define OLED_ADDR       0x3C

// Vext power control (powers OLED + LoRa)
#define VEXT_PIN        36

// USER / PRG button (active LOW)
#define BUTTON_PIN      0

// --- Timing Constants ---

// Button debounce
#define DEBOUNCE_MS     50
#define LONG_PRESS_MS   800

// Auto-boot countdown (milliseconds)
#define AUTOBOOT_TIMEOUT_MS  5000

// Splash screen duration
#define SPLASH_DURATION_MS   1500

// --- Firmware Slots ---

#define MAX_SLOTS       4

// Max stored slot-name length (null-terminated). Written by flash_firmware.py
// from the .bin filename, read at menu display time. Long names are
// truncated in the sel_cfg record; the display truncates further to fit.
#define SEL_SLOT_NAME_LEN   32

// --- Selector Config (raw flash, not in partition table) ---
// Stores lastSlot and slot filenames at a fixed flash address, independent
// of NVS. Layout is defined by sel_cfg_t in firmware_manager.cpp; this
// address + magic must match the Python flasher.
#define SEL_CFG_ADDR        0x9000
#define SEL_CFG_MAGIC       0xB0075E1C

// Sentinel value written to sel_cfg.last_slot while bootSlot() is mid-switch.
// The bootloader hook treats any value >= MAX_SLOTS as "don't save NVS".
#define SEL_SLOT_IN_FLIGHT  0xFE

// --- Per-firmware NVS isolation ---
// Each firmware slot has a 32KB NVS backup area in flash.
// Before booting a firmware, its NVS backup is copied to the main NVS
// partition. On hardware reset, the bootloader hook saves the current
// NVS back to the slot's backup area.
//
// NVS was relocated to the end of flash so active size could grow to 32KB
// without moving factory. MeshCore natively expects 20KB NVS; 32KB gives
// ~60% headroom for NVS overhead (sector headers, wear-leveling pages).

#define FLASH_SECTOR_SIZE   0x1000

#define MAIN_NVS_OFFSET     0xFB8000    // Main NVS partition (from partitions.csv)
#define MAIN_NVS_SIZE       0x8000      // 32KB (must match partitions.csv)

// NVS backup areas: 4 × 32KB = 128KB at 0xFC0000-0xFDFFFF
#define NVS_BACKUP_BASE     0xFC0000
#define NVS_BACKUP_OFFSET(n) (NVS_BACKUP_BASE + (n) * MAIN_NVS_SIZE)

// --- Per-firmware filesystem isolation ---
// Each firmware slot has a 544KB FS backup area in flash.
// Before booting a firmware, its FS backup is copied to the main SPIFFS
// partition. On return to selector, the current FS is saved to the slot's
// backup area. This prevents Meshtastic's LittleFS from destroying
// MeshCore/RNode's SPIFFS data.
//
// Layout (tail of flash, after OTA slots):
//   0xD10000 - 0xD97FFF : Active SPIFFS (544KB, in partition table)
//   0xD98000 - 0xE1FFFF : Slot 0 FS backup (544KB)
//   0xE20000 - 0xEA7FFF : Slot 1 FS backup (544KB)
//   0xEA8000 - 0xF2FFFF : Slot 2 FS backup (544KB)
//   0xF30000 - 0xFB7FFF : Slot 3 FS backup (544KB)
//   0xFB8000 - 0xFBFFFF : Active NVS (32KB, in partition table)
//   0xFC0000 - 0xFDFFFF : NVS backups (4 × 32KB = 128KB)
//   0xFE0000 - 0xFFFFFF : unused (128KB tail)

#define FS_PARTITION_OFFSET 0xD10000    // Active SPIFFS partition (from partitions.csv)
#define FS_PARTITION_SIZE   0x88000     // 544KB (must match partitions.csv)

// FS backup areas: 4 × 544KB = 2176KB at 0xD98000-0xFC7FFF
#define FS_BACKUP_BASE      0xD98000
#define FS_BACKUP_OFFSET(n) (FS_BACKUP_BASE + (n) * FS_PARTITION_SIZE)
