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

// Default slot names (used when partition has no valid app descriptor)
static const char* SLOT_NAMES[MAX_SLOTS] = {
    "Meshtastic",
    "MeshCore",
    "RNode",
    "Slot 4"
};

// --- Selector Config (raw flash, not in partition table) ---
// Stores lastSlot at a fixed flash address, independent of NVS
#define SEL_CFG_ADDR        0x9000
#define SEL_CFG_MAGIC       0xB0075E1C

// --- Per-firmware NVS isolation ---
// Each firmware slot has a 16KB NVS backup area in flash.
// Before booting a firmware, its NVS backup is copied to the main NVS
// partition (0xA000). On hardware reset, the bootloader hook saves the
// current NVS back to the slot's backup area.
// This avoids modifying the partition table, which causes boot failures.

#define FLASH_SECTOR_SIZE   0x1000

#define MAIN_NVS_OFFSET     0xA000      // Main NVS partition (from partitions.csv)
#define MAIN_NVS_SIZE       0x4000      // 16KB (must match partitions.csv)

// NVS backup areas: 4 × 16KB = 64KB at 0xD10000-0xD1FFFF
// Located between OTA slots end (0xD10000) and SPIFFS start (0xD20000)
#define NVS_BACKUP_BASE     0xD10000
#define NVS_BACKUP_OFFSET(n) (NVS_BACKUP_BASE + (n) * MAIN_NVS_SIZE)

// --- Per-firmware filesystem isolation ---
// Each firmware slot has a 576KB FS backup area in flash.
// Before booting a firmware, its FS backup is copied to the main SPIFFS
// partition (0xD20000). On return to selector, the current FS is saved
// to the slot's backup area. This prevents Meshtastic's LittleFS from
// destroying MeshCore/RNode's SPIFFS data.
//
// Layout:
//   0xD10000 - 0xD1FFFF : NVS backups (4 × 16KB = 64KB)
//   0xD20000 - 0xDAFFFF : Active SPIFFS (576KB, in partition table)
//   0xDB0000 - 0xE3FFFF : Slot 0 FS backup (576KB)
//   0xE40000 - 0xECFFFF : Slot 1 FS backup (576KB)
//   0xED0000 - 0xF5FFFF : Slot 2 FS backup (576KB)
//   0xF60000 - 0xFEFFFF : Slot 3 FS backup (576KB)
//   0xFF0000 - 0xFFFFFF : Unused (64KB)

#define FS_PARTITION_OFFSET 0xD20000    // Active SPIFFS partition (from partitions.csv)
#define FS_PARTITION_SIZE   0x90000     // 576KB (must match partitions.csv)

// FS backup areas: 4 × 576KB = 2.25MB at 0xDB0000-0xFEFFFF
#define FS_BACKUP_BASE      0xDB0000
#define FS_BACKUP_OFFSET(n) (FS_BACKUP_BASE + (n) * FS_PARTITION_SIZE)
