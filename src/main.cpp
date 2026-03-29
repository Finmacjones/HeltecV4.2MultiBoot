#include <Arduino.h>
#include <nvs_flash.h>
#include "config.h"
#include "display.h"
#include "button.h"
#include "firmware_manager.h"
#include "menu.h"

void setup() {
    Serial.begin(115200);
    delay(500);
    Serial.println();
    Serial.println("================================");
    Serial.println("  V4 Multi-Boot Selector v0.1");
    Serial.println("================================");

    // Save the previous firmware's NVS to its backup area BEFORE
    // nvs_flash_init() might erase it. This is a safety net in case
    // the bootloader hook's save didn't run (e.g., watchdog reset).
    FirmwareManager::saveCurrentNvs();

    // Initialize NVS (selector's own NVS at 0xA000)
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }

    // Initialize subsystems
    Display::init();
    Button::init();

    // Save the previous firmware's filesystem to its backup area.
    // Done after Display::init() so we can show status on OLED.
    // This is a safety net — the main save happens in bootSlot() before
    // switching, but if the firmware crashed or was hardware-reset, the
    // bootloader hook only saves NVS (FS is too large for bootloader).
    Display::showStatus("Saving data...", "Please wait");
    FirmwareManager::saveCurrentFs();

    // Check if USER button is held at boot (skip auto-boot, show menu)
    bool forceMenu = Button::isHeld();
    if (forceMenu) {
        Serial.println("[BOOT] USER button held - skipping auto-boot");
    }

    // Initialize menu state machine
    Menu::init(forceMenu);
}

void loop() {
    Menu::tick();
    delay(10); // ~100Hz polling
}
