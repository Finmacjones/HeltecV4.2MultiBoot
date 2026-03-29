#pragma once

#include <cstdint>

enum class ButtonEvent {
    NONE,
    SHORT_PRESS,
    LONG_PRESS
};

namespace Button {

// Initialize GPIO and interrupt for the USER button
void init();

// Poll for button events (call from loop)
ButtonEvent poll();

// Check if button is currently held (for boot-time check)
bool isHeld();

} // namespace Button
