#pragma once

#include "config.h"
#include <cstdint>

struct FirmwareSlot {
    bool valid;              // true if partition contains a valid firmware
    char name[32];           // project name from app descriptor
    char version[32];        // version string from app descriptor
    int partitionIndex;      // OTA partition subtype index (0-3)
};

namespace FirmwareManager {

// Scan all OTA partitions and populate slot info
void scan(FirmwareSlot* slots, int* count);

// Set the given OTA slot as the next boot target and restart
// Returns false if the slot is invalid (does not restart)
bool bootSlot(int slotIndex);

// Get the last-booted slot index from NVS (-1 if none)
int getLastSlot();

// Save the last-booted slot index to NVS
void setLastSlot(int slotIndex);

// Save previous firmware's NVS to its backup area.
// Safety net for reset paths where the bootloader hook didn't save.
void saveCurrentNvs();

// Save previous firmware's filesystem to its backup area.
// Called after display init so we can show progress on OLED.
void saveCurrentFs();

// Return the stored .bin filename for the given slot, or a "Slot N" fallback
// if sel_cfg is missing/empty. Pointer is valid until the next call.
const char* getSlotName(int slotIndex);

} // namespace FirmwareManager
