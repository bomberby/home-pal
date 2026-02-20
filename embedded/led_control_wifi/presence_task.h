#ifndef BLE_TRACKER_H
#define BLE_TRACKER_H

#include <Arduino.h>

// Call this in your void setup() in the .ino file
void setupBLETracker(const char* targetUuid);

void sendHealthAndRSSI(int rssi);
#endif