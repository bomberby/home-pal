#ifndef BLE_TRACKER_H
#define BLE_TRACKER_H

#include <Arduino.h>
#include <freertos/semphr.h>

extern SemaphoreHandle_t mqttMutex;  // defined in led_control_wifi.ino

// Call this in your void setup() in the .ino file
void setupBLETracker(const char* targetUuid);

void sendHealthAndRSSI(int rssi);
#endif