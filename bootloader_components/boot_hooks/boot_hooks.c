/*
 * Custom bootloader hook for V4 Multi-Boot Selector
 *
 * On every boot, checks the CPU reset reason:
 *  - Software reset (esp_restart): pass through, let firmware boot
 *  - Deep sleep wake: pass through
 *  - Everything else (RST button, power-on, watchdog, brownout):
 *    1. Save the running firmware's NVS data to its per-slot backup area
 *    2. Erase otadata → forces factory partition (selector) boot
 *
 * NVS isolation: each firmware slot has a 16KB backup area in flash.
 * The selector copies the backup to the main NVS before booting a firmware.
 * This hook saves the NVS back when returning to the selector.
 */

#include "esp_log.h"
#include "soc/rtc_cntl_reg.h"
#include "esp32s3/rom/spi_flash.h"

static const char *TAG = "boot_hooks";

void bootloader_hooks_include(void) { }

/* Must match partitions.csv / config.h */
#define OTADATA_OFFSET      0xE000
#define FLASH_SECTOR_SZ     0x1000

/* Selector config at fixed flash address */
#define SEL_CFG_ADDR        0x9000
#define SEL_CFG_MAGIC       0xB0075E1C
#define MAX_SLOTS           4

/* NVS partition and per-slot backup areas (see src/config.h for full map) */
#define MAIN_NVS_OFFSET     0xFB8000
#define MAIN_NVS_SIZE       0x8000      /* 32KB, 8 sectors */
#define NVS_BACKUP_BASE     0xFC0000    /* 4 × 32KB = 128KB */

/* ESP32-S3 reset reasons */
#define RESET_SW_SYS        3
#define RESET_DEEPSLEEP     5
#define RESET_SW_CPU       12

/* Selector config struct (must match firmware_manager.cpp) */
typedef struct {
    uint32_t magic;
    uint8_t  last_slot;
    uint8_t  reserved[27];
} __attribute__((packed)) sel_cfg_t;

/* 4KB buffer for flash copy operations */
static uint8_t flash_buf[FLASH_SECTOR_SZ] __attribute__((aligned(4)));

/*
 * Copy NVS data between main partition and a slot's backup area.
 * dir=0: main → backup (save)
 * dir=1: backup → main (restore)
 */
static void copy_nvs(int slot, int dir)
{
    uint32_t backup_addr = NVS_BACKUP_BASE + slot * MAIN_NVS_SIZE;
    int sectors = MAIN_NVS_SIZE / FLASH_SECTOR_SZ;

    for (int i = 0; i < sectors; i++) {
        uint32_t src = (dir == 0)
            ? MAIN_NVS_OFFSET + i * FLASH_SECTOR_SZ
            : backup_addr + i * FLASH_SECTOR_SZ;
        uint32_t dst = (dir == 0)
            ? backup_addr + i * FLASH_SECTOR_SZ
            : MAIN_NVS_OFFSET + i * FLASH_SECTOR_SZ;

        if (esp_rom_spiflash_read(src, (uint32_t *)flash_buf, FLASH_SECTOR_SZ) != 0) {
            ESP_LOGE(TAG, "NVS copy: read failed at 0x%x", src);
            return;
        }
        if (esp_rom_spiflash_erase_sector(dst / FLASH_SECTOR_SZ) != 0) {
            ESP_LOGE(TAG, "NVS copy: erase failed at 0x%x", dst);
            return;
        }
        if (esp_rom_spiflash_write(dst, (uint32_t *)flash_buf, FLASH_SECTOR_SZ) != 0) {
            ESP_LOGE(TAG, "NVS copy: write failed at 0x%x", dst);
            return;
        }
    }

    ESP_LOGI(TAG, "NVS %s slot %d (0x%x)",
             dir == 0 ? "saved to" : "restored from",
             slot, backup_addr);
}

void bootloader_after_init(void)
{
    uint32_t reason = REG_GET_FIELD(RTC_CNTL_RESET_STATE_REG,
                                    RTC_CNTL_RESET_CAUSE_PROCPU);

    if (reason == RESET_SW_SYS ||
        reason == RESET_DEEPSLEEP ||
        reason == RESET_SW_CPU) {
        ESP_LOGI(TAG, "Software/sleep reset (reason=%d), booting firmware", (int)reason);
        return;
    }

    ESP_LOGI(TAG, "Hardware reset (reason=%d), restoring selector", (int)reason);

    /* Save the running firmware's NVS to its backup area */
    sel_cfg_t cfg __attribute__((aligned(4)));
    if (esp_rom_spiflash_read(SEL_CFG_ADDR, (uint32_t *)&cfg, sizeof(cfg)) == 0 &&
        cfg.magic == SEL_CFG_MAGIC &&
        cfg.last_slot < MAX_SLOTS) {
        ESP_LOGI(TAG, "Saving NVS for slot %d", (int)cfg.last_slot);
        copy_nvs(cfg.last_slot, 0);  /* main → backup */
    }

    /* Erase otadata to force factory (selector) boot */
    esp_rom_spiflash_erase_sector(OTADATA_OFFSET / FLASH_SECTOR_SZ);
    esp_rom_spiflash_erase_sector((OTADATA_OFFSET + FLASH_SECTOR_SZ) / FLASH_SECTOR_SZ);
}
