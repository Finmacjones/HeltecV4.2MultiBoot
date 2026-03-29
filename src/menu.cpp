#include "menu.h"
#include "config.h"
#include "display.h"
#include "button.h"
#include "firmware_manager.h"
#include <Arduino.h>

enum class MenuState {
    SPLASH,
    MENU,
    BOOTING
};

static MenuState state = MenuState::SPLASH;
static FirmwareSlot slots[MAX_SLOTS];
static int slotCount = 0;
static int selected = 0;
static int lastSlot = -1;
static uint32_t stateStartMs = 0;
static bool autoBootActive = false;

// Find the next valid slot in the given direction (+1 or -1), wrapping around.
// Returns -1 if no valid slots exist.
static int findNextValid(int from, int direction) {
    for (int i = 1; i <= slotCount; i++) {
        int idx = (from + i * direction + slotCount) % slotCount;
        if (slots[idx].valid) return idx;
    }
    return -1;
}

static bool anyValidSlot() {
    for (int i = 0; i < slotCount; i++) {
        if (slots[i].valid) return true;
    }
    return false;
}

namespace Menu {

void init(bool forceMenu) {
    // Scan firmware partitions
    FirmwareManager::scan(slots, &slotCount);

    // Check last-booted slot for auto-boot
    lastSlot = FirmwareManager::getLastSlot();

    if (!anyValidSlot()) {
        // No firmware installed at all
        Display::showEmpty();
        state = MenuState::MENU; // stay in menu (will show empty)
        return;
    }

    // Start with splash
    state = MenuState::SPLASH;
    stateStartMs = millis();
    Display::showSplash();

    // Pre-select last booted slot, or first valid
    if (lastSlot >= 0 && lastSlot < slotCount && slots[lastSlot].valid) {
        selected = lastSlot;
        autoBootActive = !forceMenu;
    } else {
        selected = findNextValid(-1, 1);
        if (selected < 0) selected = 0;
        autoBootActive = false;
    }
}

void tick() {
    uint32_t now = millis();
    ButtonEvent evt = Button::poll();

    switch (state) {
    case MenuState::SPLASH: {
        // Any button press skips splash
        if (evt != ButtonEvent::NONE) {
            autoBootActive = false;
        }

        if (now - stateStartMs >= SPLASH_DURATION_MS) {
            state = MenuState::MENU;
            stateStartMs = now;

            if (!anyValidSlot()) {
                Display::showEmpty();
            } else {
                int countdown = autoBootActive ? AUTOBOOT_TIMEOUT_MS : -1;
                Display::showMenu(slots, slotCount, selected, countdown);
            }
        }
        break;
    }

    case MenuState::MENU: {
        if (!anyValidSlot()) {
            // Nothing to do, just wait
            break;
        }

        bool needRedraw = false;

        if (evt == ButtonEvent::SHORT_PRESS) {
            if (autoBootActive) {
                // First press just cancels auto-boot, stays on current slot
                autoBootActive = false;
                needRedraw = true;
            } else {
                // Navigate to next valid slot
                int next = findNextValid(selected, 1);
                if (next >= 0 && next != selected) {
                    selected = next;
                    needRedraw = true;
                }
            }
        }

        if (evt == ButtonEvent::LONG_PRESS) {
            // Select current slot and boot
            if (slots[selected].valid) {
                state = MenuState::BOOTING;
                Display::showBooting(slots[selected].name);
                stateStartMs = now;
            }
            break;
        }

        // Auto-boot countdown
        if (autoBootActive) {
            int elapsed = now - stateStartMs;
            int remaining = AUTOBOOT_TIMEOUT_MS - elapsed;

            if (remaining <= 0) {
                // Auto-boot!
                state = MenuState::BOOTING;
                Display::showBooting(slots[selected].name);
                stateStartMs = now;
                break;
            }

            // Redraw every ~100ms for smooth countdown bar
            static uint32_t lastRedraw = 0;
            if (now - lastRedraw >= 100) {
                lastRedraw = now;
                needRedraw = true;
            }
        }

        if (needRedraw) {
            int countdown = autoBootActive
                ? (AUTOBOOT_TIMEOUT_MS - (int)(now - stateStartMs))
                : -1;
            Display::showMenu(slots, slotCount, selected, countdown);
        }
        break;
    }

    case MenuState::BOOTING: {
        // Brief delay to show the "Booting..." screen
        if (now - stateStartMs >= 500) {
            Serial.printf("[MENU] Booting slot %d: %s\n", selected, slots[selected].name);
            if (!FirmwareManager::bootSlot(selected)) {
                // Boot failed
                Display::showError("Boot failed!", "Firmware may be corrupt");
                delay(2000);
                state = MenuState::MENU;
                stateStartMs = now;
                Display::showMenu(slots, slotCount, selected, -1);
            }
            // If bootSlot succeeds, it calls esp_restart() and never returns
        }
        break;
    }
    }
}

} // namespace Menu
