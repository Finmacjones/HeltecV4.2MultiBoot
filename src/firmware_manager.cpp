#include "firmware_manager.h"
#include "config.h"
#include "display.h"
#include <Arduino.h>
#include <esp_crc.h>
#include <esp_ota_ops.h>
#include <esp_partition.h>
#include <esp_spi_flash.h>
#include <esp_system.h>
#include <nvs_flash.h>
#include <string.h>

// Selector config stored in raw flash at SEL_CFG_ADDR
typedef struct {
    uint32_t magic;
    uint8_t  last_slot;
    uint8_t  reserved[27];
} __attribute__((packed)) sel_cfg_t;

namespace FirmwareManager {

void scan(FirmwareSlot* slots, int* count) {
    *count = 0;

    for (int i = 0; i < MAX_SLOTS; i++) {
        slots[i].valid = false;
        slots[i].name[0] = '\0';
        slots[i].version[0] = '\0';
        slots[i].partitionIndex = i;

        const esp_partition_t* part = esp_partition_find_first(
            ESP_PARTITION_TYPE_APP,
            (esp_partition_subtype_t)(ESP_PARTITION_SUBTYPE_APP_OTA_0 + i),
            NULL
        );

        if (part == NULL) {
            Serial.printf("[FW] Slot %d: partition not found\n", i);
            (*count)++;
            continue;
        }

        // Check for ESP image magic byte (0xE9) to detect populated slots
        uint8_t magic = 0;
        esp_partition_read(part, 0, &magic, 1);

        if (magic == 0xE9) {
            slots[i].valid = true;

            // Try to read app descriptor for name/version (optional)
            esp_app_desc_t desc;
            if (esp_ota_get_partition_description(part, &desc) == ESP_OK) {
                strncpy(slots[i].name, desc.project_name, sizeof(slots[i].name) - 1);
                slots[i].name[sizeof(slots[i].name) - 1] = '\0';
                strncpy(slots[i].version, desc.version, sizeof(slots[i].version) - 1);
                slots[i].version[sizeof(slots[i].version) - 1] = '\0';
            }

            Serial.printf("[FW] Slot %d: valid image (name=%s, ver=%s)\n", i,
                          slots[i].name[0] ? slots[i].name : SLOT_NAMES[i],
                          slots[i].version[0] ? slots[i].version : "unknown");
        } else {
            Serial.printf("[FW] Slot %d: empty (magic=0x%02X)\n", i, magic);
        }

        (*count)++;
    }
}

// --- Selector Config (raw flash at SEL_CFG_ADDR) ---

int getLastSlot() {
    sel_cfg_t cfg;
    if (spi_flash_read(SEL_CFG_ADDR, &cfg, sizeof(cfg)) != ESP_OK) {
        return -1;
    }
    if (cfg.magic != SEL_CFG_MAGIC) {
        return -1;
    }
    if (cfg.last_slot >= MAX_SLOTS) {
        return -1;
    }
    return (int)cfg.last_slot;
}

void setLastSlot(int slotIndex) {
    sel_cfg_t cfg;
    memset(&cfg, 0xFF, sizeof(cfg));
    cfg.magic = SEL_CFG_MAGIC;
    cfg.last_slot = (uint8_t)slotIndex;

    // Erase the 4KB sector at SEL_CFG_ADDR, then write
    if (spi_flash_erase_sector(SEL_CFG_ADDR / FLASH_SECTOR_SIZE) != ESP_OK) {
        Serial.println("[FW] Failed to erase sel_cfg sector");
        return;
    }
    if (spi_flash_write(SEL_CFG_ADDR, &cfg, sizeof(cfg)) != ESP_OK) {
        Serial.println("[FW] Failed to write sel_cfg");
        return;
    }
    Serial.printf("[FW] Saved lastSlot=%d to sel_cfg\n", slotIndex);
}

// --- Flash Copy Helper ---

static void copyFlash(uint32_t src_base, uint32_t dst_base, uint32_t size) {
    static uint8_t buf[FLASH_SECTOR_SIZE] __attribute__((aligned(4)));
    int sectors = size / FLASH_SECTOR_SIZE;

    for (int i = 0; i < sectors; i++) {
        uint32_t src = src_base + i * FLASH_SECTOR_SIZE;
        uint32_t dst = dst_base + i * FLASH_SECTOR_SIZE;

        if (spi_flash_read(src, buf, FLASH_SECTOR_SIZE) != ESP_OK) {
            Serial.printf("[FW] Flash copy: read failed at 0x%X\n", src);
            return;
        }
        if (spi_flash_erase_sector(dst / FLASH_SECTOR_SIZE) != ESP_OK) {
            Serial.printf("[FW] Flash copy: erase failed at 0x%X\n", dst);
            return;
        }
        if (spi_flash_write(dst, buf, FLASH_SECTOR_SIZE) != ESP_OK) {
            Serial.printf("[FW] Flash copy: write failed at 0x%X\n", dst);
            return;
        }
    }
}

void saveCurrentNvs() {
    // Read sel_cfg to find which slot was last active
    sel_cfg_t cfg;
    if (spi_flash_read(SEL_CFG_ADDR, &cfg, sizeof(cfg)) != ESP_OK) return;
    if (cfg.magic != SEL_CFG_MAGIC) return;
    if (cfg.last_slot >= MAX_SLOTS) return;

    uint32_t backup_addr = NVS_BACKUP_OFFSET(cfg.last_slot);
    Serial.printf("[FW] Saving NVS for slot %d to 0x%X (before nvs_flash_init)\n",
                  cfg.last_slot, backup_addr);

    // Copy main NVS → slot backup (before nvs_flash_init() might erase it)
    copyFlash(MAIN_NVS_OFFSET, backup_addr, MAIN_NVS_SIZE);
    Serial.println("[FW] NVS saved OK");
}

// --- Filesystem Data Swap (per-firmware isolation) ---

void saveCurrentFs() {
    // Read sel_cfg to find which slot was last active
    sel_cfg_t cfg;
    if (spi_flash_read(SEL_CFG_ADDR, &cfg, sizeof(cfg)) != ESP_OK) return;
    if (cfg.magic != SEL_CFG_MAGIC) return;
    if (cfg.last_slot >= MAX_SLOTS) return;

    uint32_t backup_addr = FS_BACKUP_OFFSET(cfg.last_slot);
    Serial.printf("[FW] Saving FS for slot %d to 0x%X (%dKB)\n",
                  cfg.last_slot, backup_addr, FS_PARTITION_SIZE / 1024);

    copyFlash(FS_PARTITION_OFFSET, backup_addr, FS_PARTITION_SIZE);
    Serial.println("[FW] FS saved OK");
}

static void restoreSlotFs(int slotIndex) {
    uint32_t backup_addr = FS_BACKUP_OFFSET(slotIndex);
    Serial.printf("[FW] Restoring FS for slot %d from 0x%X (%dKB)\n",
                  slotIndex, backup_addr, FS_PARTITION_SIZE / 1024);

    copyFlash(backup_addr, FS_PARTITION_OFFSET, FS_PARTITION_SIZE);
    Serial.println("[FW] FS restored OK");
}

// --- Direct OTA Data Write (bypasses esp_ota_set_boot_partition verification) ---

#define OTADATA_OFFSET  0xE000

typedef struct {
    uint32_t ota_seq;       // 1-based: slot 0 = seq 1, slot 1 = seq 2, etc.
    uint8_t  seq_label[20]; // zeros
    uint32_t ota_state;     // 0xFFFFFFFF = ESP_OTA_IMG_UNDEFINED
    uint32_t crc;           // CRC32 of first 28 bytes
} __attribute__((packed)) ota_select_entry_t;

static bool writeOtaData(int slotIndex) {
    ota_select_entry_t entry;
    memset(&entry, 0, sizeof(entry));
    entry.ota_seq = (uint32_t)(slotIndex + 1);
    entry.ota_state = 0xFFFFFFFF; // ESP_OTA_IMG_UNDEFINED

    // CRC32 over first 28 bytes (everything except the crc field itself)
    entry.crc = esp_crc32_le(UINT32_MAX, (const uint8_t*)&entry, 28);

    // Erase both otadata sectors (4KB each)
    if (spi_flash_erase_sector(OTADATA_OFFSET / FLASH_SECTOR_SIZE) != ESP_OK) {
        Serial.println("[FW] Failed to erase otadata sector 0");
        return false;
    }
    if (spi_flash_erase_sector((OTADATA_OFFSET + FLASH_SECTOR_SIZE) / FLASH_SECTOR_SIZE) != ESP_OK) {
        Serial.println("[FW] Failed to erase otadata sector 1");
        return false;
    }

    // Write entry to sector 0
    if (spi_flash_write(OTADATA_OFFSET, &entry, sizeof(entry)) != ESP_OK) {
        Serial.println("[FW] Failed to write otadata");
        return false;
    }

    Serial.printf("[FW] Wrote otadata: ota_seq=%d, crc=0x%08X\n",
                  entry.ota_seq, entry.crc);
    return true;
}

// --- Boot ---

bool bootSlot(int slotIndex) {
    if (slotIndex < 0 || slotIndex >= MAX_SLOTS) {
        Serial.printf("[FW] Invalid slot index: %d\n", slotIndex);
        return false;
    }

    const esp_partition_t* part = esp_partition_find_first(
        ESP_PARTITION_TYPE_APP,
        (esp_partition_subtype_t)(ESP_PARTITION_SUBTYPE_APP_OTA_0 + slotIndex),
        NULL
    );

    if (part == NULL) {
        Serial.printf("[FW] Slot %d: partition not found\n", slotIndex);
        return false;
    }

    Serial.printf("[FW] Booting slot %d (%s)\n", slotIndex, SLOT_NAMES[slotIndex]);

    // Save current slot's data before switching (primary save path)
    int prevSlot = getLastSlot();
    if (prevSlot >= 0 && prevSlot < MAX_SLOTS) {
        // Deinit NVS once before any flash operations
        nvs_flash_deinit();

        Display::showStatus("Saving NVS...", SLOT_NAMES[prevSlot]);
        copyFlash(MAIN_NVS_OFFSET, NVS_BACKUP_OFFSET(prevSlot), MAIN_NVS_SIZE);
        Serial.printf("[FW] Saved NVS for slot %d\n", prevSlot);

        Display::showStatus("Saving FS...", SLOT_NAMES[prevSlot]);
        copyFlash(FS_PARTITION_OFFSET, FS_BACKUP_OFFSET(prevSlot), FS_PARTITION_SIZE);
        Serial.printf("[FW] Saved FS for slot %d\n", prevSlot);
    } else {
        nvs_flash_deinit();
    }

    // Record which slot we're about to boot
    setLastSlot(slotIndex);

    // Restore new slot's NVS backup to the main NVS partition
    Display::showStatus("Loading NVS...", SLOT_NAMES[slotIndex]);
    copyFlash(NVS_BACKUP_OFFSET(slotIndex), MAIN_NVS_OFFSET, MAIN_NVS_SIZE);
    Serial.printf("[FW] Restored NVS for slot %d\n", slotIndex);

    // Restore new slot's filesystem backup to the main SPIFFS partition
    Display::showStatus("Loading FS...", SLOT_NAMES[slotIndex]);
    restoreSlotFs(slotIndex);

    // Set OTA boot target
    esp_err_t err = esp_ota_set_boot_partition(part);
    if (err != ESP_OK) {
        Serial.printf("[FW] esp_ota_set_boot_partition failed: 0x%x, using direct otadata write\n", err);
        if (!writeOtaData(slotIndex)) {
            Serial.println("[FW] Direct otadata write also failed");
            return false;
        }
    }

    Serial.println("[FW] Restarting...");
    delay(100);
    esp_restart();

    return true; // unreachable
}

} // namespace FirmwareManager
