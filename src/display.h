#pragma once

#include <U8g2lib.h>

// Forward declaration
struct FirmwareSlot;

namespace Display {

// Initialize the OLED (Vext power, reset pulse, I2C)
void init();

// Show the boot splash screen
void showSplash();

// Show the firmware selection menu
// slots: array of firmware slot info
// count: number of slots
// selected: currently highlighted index
// countdownMs: remaining auto-boot time (-1 = no countdown)
void showMenu(const FirmwareSlot* slots, int count, int selected, int countdownMs);

// Show "Booting..." screen with firmware name
void showBooting(const char* name);

// Show a status message (e.g., "Saving data...", "Loading data...")
void showStatus(const char* line1, const char* line2 = nullptr);

// Show an error message
void showError(const char* line1, const char* line2 = nullptr);

// Show "No firmware installed" screen
void showEmpty();

// Get the U8g2 instance (for advanced use)
U8G2& getDisplay();

} // namespace Display
