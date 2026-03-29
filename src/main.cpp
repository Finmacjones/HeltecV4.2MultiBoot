#include <Arduino.h>
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

    // Safety-net save of the previous firmware's NVS, in case the bootloader
    // hook didn't run (unusual reset type). The selector itself never mounts
    // or writes NVS — doing so on a partition that still holds firmware data
    // risks nvs_flash_init() → nvs_flash_erase() wiping that data.
    FirmwareManager::saveCurrentNvs();

    // Initialize subsystems
    Display::init();
    Button::init();

    // Safety-net save of the previous firmware's filesystem. The main save
    // runs in bootSlot() before switching; this covers crash / hardware-reset
    // paths where the bootloader hook only saved NVS (FS is too large to copy
    // from the bootloader).
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
