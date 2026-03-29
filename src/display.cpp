#include "display.h"
#include "config.h"
#include "firmware_manager.h"
#include <Arduino.h>
#include <Wire.h>

// SSD1315 128x64 OLED over I2C (SSD1306-compatible driver)
static U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(
    U8G2_R0,
    OLED_RST,
    OLED_SCL,
    OLED_SDA
);

static const int SCREEN_W = 128;
static const int SCREEN_H = 64;

namespace Display {

void init() {
    // Enable Vext power rail (active LOW on Heltec V4)
    pinMode(VEXT_PIN, OUTPUT);
    digitalWrite(VEXT_PIN, LOW);
    delay(20);

    u8g2.begin();
    u8g2.setContrast(200);
    u8g2.clearBuffer();
    u8g2.sendBuffer();
}

void showSplash() {
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_helvB12_tr);

    const char* title = "V4 MultiBoot";
    int tw = u8g2.getStrWidth(title);
    u8g2.drawStr((SCREEN_W - tw) / 2, 28, title);

    u8g2.setFont(u8g2_font_helvR08_tr);
    const char* sub = "Firmware Selector v0.1";
    int sw = u8g2.getStrWidth(sub);
    u8g2.drawStr((SCREEN_W - sw) / 2, 44, sub);

    // Draw border
    u8g2.drawFrame(0, 0, SCREEN_W, SCREEN_H);

    u8g2.sendBuffer();
}

void showMenu(const FirmwareSlot* slots, int count, int selected, int countdownMs) {
    u8g2.clearBuffer();

    // Title bar
    u8g2.setFont(u8g2_font_helvB08_tr);
    u8g2.drawStr(2, 10, "Select Firmware:");
    u8g2.drawHLine(0, 13, SCREEN_W);

    // Menu items (up to 4 visible, 12px per row starting at y=16)
    const int itemH = 12;
    const int startY = 16;

    for (int i = 0; i < count && i < MAX_SLOTS; i++) {
        int y = startY + i * itemH;

        if (i == selected) {
            // Highlight bar
            u8g2.setDrawColor(1);
            u8g2.drawBox(0, y, SCREEN_W, itemH);
            u8g2.setDrawColor(0);
        }

        // Use hardcoded slot names instead of firmware descriptor names
        u8g2.setFont(u8g2_font_helvR08_tr);
        char label[32];
        if (i == selected) {
            // Arrow + name for selected item
            if (slots[i].valid) {
                snprintf(label, sizeof(label), "> %d. %s", i + 1, SLOT_NAMES[i]);
            } else {
                snprintf(label, sizeof(label), "> %d. %s (empty)", i + 1, SLOT_NAMES[i]);
            }
        } else {
            if (slots[i].valid) {
                snprintf(label, sizeof(label), "  %d. %s", i + 1, SLOT_NAMES[i]);
            } else {
                snprintf(label, sizeof(label), "  %d. %s (empty)", i + 1, SLOT_NAMES[i]);
            }
        }
        u8g2.drawStr(2, y + 10, label);

        u8g2.setDrawColor(1);
    }

    // Countdown bar at bottom
    if (countdownMs > 0) {
        int barW = (int)((long)countdownMs * SCREEN_W / AUTOBOOT_TIMEOUT_MS);
        if (barW > SCREEN_W) barW = SCREEN_W;
        u8g2.drawBox(0, SCREEN_H - 3, barW, 3);
    }

    u8g2.sendBuffer();
}

void showBooting(const char* name) {
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_helvB10_tr);

    const char* msg = "Booting...";
    int mw = u8g2.getStrWidth(msg);
    u8g2.drawStr((SCREEN_W - mw) / 2, 26, msg);

    u8g2.setFont(u8g2_font_helvR08_tr);
    int nw = u8g2.getStrWidth(name);
    u8g2.drawStr((SCREEN_W - nw) / 2, 44, name);

    u8g2.sendBuffer();
}

void showStatus(const char* line1, const char* line2) {
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_helvB10_tr);

    int mw = u8g2.getStrWidth(line1);
    u8g2.drawStr((SCREEN_W - mw) / 2, 26, line1);

    if (line2) {
        u8g2.setFont(u8g2_font_helvR08_tr);
        int sw = u8g2.getStrWidth(line2);
        u8g2.drawStr((SCREEN_W - sw) / 2, 44, line2);
    }

    u8g2.sendBuffer();
}

void showError(const char* line1, const char* line2) {
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_helvB08_tr);

    u8g2.drawStr(2, 12, "ERROR");
    u8g2.drawHLine(0, 15, SCREEN_W);

    u8g2.setFont(u8g2_font_helvR08_tr);
    if (line1) u8g2.drawStr(4, 32, line1);
    if (line2) u8g2.drawStr(4, 48, line2);

    u8g2.sendBuffer();
}

void showEmpty() {
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_helvB10_tr);

    const char* msg = "No Firmware";
    int mw = u8g2.getStrWidth(msg);
    u8g2.drawStr((SCREEN_W - mw) / 2, 24, msg);

    u8g2.setFont(u8g2_font_helvR08_tr);
    const char* line1 = "Use flash_firmware.py";
    const char* line2 = "to install firmware";
    int w1 = u8g2.getStrWidth(line1);
    int w2 = u8g2.getStrWidth(line2);
    u8g2.drawStr((SCREEN_W - w1) / 2, 40, line1);
    u8g2.drawStr((SCREEN_W - w2) / 2, 54, line2);

    u8g2.sendBuffer();
}

U8G2& getDisplay() {
    return u8g2;
}

} // namespace Display
