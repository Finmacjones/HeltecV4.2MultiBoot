#pragma once

namespace Menu {

// Initialize the menu system (call after Display, Button, FirmwareManager init)
// forceMenu: if true, skip auto-boot and show menu immediately
void init(bool forceMenu = false);

// Run one tick of the menu state machine (call from loop)
void tick();

} // namespace Menu
