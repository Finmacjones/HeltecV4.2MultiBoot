#include "button.h"
#include "config.h"
#include <Arduino.h>

static volatile bool buttonPressed = false;
static uint32_t pressStart = 0;
static bool waitingRelease = false;

static void IRAM_ATTR buttonISR() {
    buttonPressed = true;
}

namespace Button {

void init() {
    pinMode(BUTTON_PIN, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(BUTTON_PIN), buttonISR, FALLING);
}

ButtonEvent poll() {
    uint32_t now = millis();

    // Button just pressed (ISR fired)
    if (buttonPressed && !waitingRelease) {
        buttonPressed = false;
        // Debounce: ignore if too soon
        if (now - pressStart < DEBOUNCE_MS) {
            return ButtonEvent::NONE;
        }
        pressStart = now;
        waitingRelease = true;
        return ButtonEvent::NONE;
    }

    // Waiting for release
    if (waitingRelease) {
        bool currentState = digitalRead(BUTTON_PIN) == LOW;

        if (!currentState) {
            // Button released
            waitingRelease = false;
            uint32_t duration = now - pressStart;

            if (duration >= LONG_PRESS_MS) {
                return ButtonEvent::LONG_PRESS;
            } else if (duration >= DEBOUNCE_MS) {
                return ButtonEvent::SHORT_PRESS;
            }
        }
        // Clear any ISR flag that fired during hold
        buttonPressed = false;
    }

    return ButtonEvent::NONE;
}

bool isHeld() {
    return digitalRead(BUTTON_PIN) == LOW;
}

} // namespace Button
