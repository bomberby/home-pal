#include "presence_task.h"
#include <BLEDevice.h>
#include <BLEUtils.h>
#include <BLEScan.h>
#include <PubSubClient.h> // Ensure this is installed
#include <WiFi.h>
#include <ArduinoJson.h>
#include "secrets.h"

// External objects from your main sketch (to reuse your existing MQTT connection)
extern PubSubClient mqttClient; 

static String targetBLEUUID;
static uint32_t lastScanTime = 0;
const uint32_t SCAN_INTERVAL = 10000; // Scan every 10 seconds

// The Background Task Function
void bleTask(void * pvParameters) {
  BLEDevice::init("ESP32-C6-Tracker");
  BLEScan* pBLEScan = BLEDevice::getScan();
  pBLEScan->setActiveScan(false); // Passive is better for coexistence
  pBLEScan->setInterval(100);
  pBLEScan->setWindow(99);

  for(;;) {
      // Run scan for 2 seconds
      BLEScanResults* foundDevices = pBLEScan->start(2, false);
      Serial.println();
      bool found = false;
      int rssi;
      for (int i = 0; i < foundDevices->getCount(); i++) {
        BLEAdvertisedDevice device = foundDevices->getDevice(i);
        if (device.haveManufacturerData()) {
          String mData = device.getManufacturerData();
          
          // iBeacon packets are exactly 25 bytes. 
          // Apple's Manufacturer ID is 0x4C (76 in decimal)
          if (mData.length() >= 25 && mData[0] == 0x4C && mData[1] == 0x00) {
            // This extracts the UUID part of the iBeacon packet
            char uuidStr[37];
            sprintf(uuidStr, "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
                    mData[4], mData[5], mData[6], mData[7],
                    mData[8], mData[9], mData[10], mData[11],
                    mData[12], mData[13], mData[14], mData[15],
                    mData[16], mData[17], mData[18], mData[19]);

            Serial.print("Detected iBeacon RSSI: ");
            Serial.print(device.getRSSI());
            Serial.print(" UUID: ");
            Serial.println(uuidStr);

            // Compare with the target UUID (make sure targetBLEUUID is lowercase)
            if (String(uuidStr) == BLE_UUID) {
              found = true;
              rssi = device.getRSSI();
              if (mqttClient.connected()) {
                sendHealthAndRSSI(rssi);
              }
              break; 
            }
          }
        }
      }

      if (!found) {
        Serial.println("Did not found matching device");
        sendHealthAndRSSI(-100);
      }
      
      pBLEScan->clearResults();
      
      // Wait 10 seconds. This allows the CPU to focus entirely on your LED Task
      vTaskDelay(SCAN_INTERVAL / portTICK_PERIOD_MS);
  }
}

void setupBLETracker(const char* targetUuid) {
    targetBLEUUID = String(targetUuid);
    targetBLEUUID.toLowerCase();

    // Create the background task on the single core
    // Priority 1 is lower than most LED tasks (usually 2 or 3), preventing flicker
    xTaskCreate(bleTask, "BLE_RSSI_Task", 4096, NULL, 1, NULL);
}



float voltageToPercentage(float voltage) {
  // Define the minimum and maximum voltages for a single LiPo cell
  const float MIN_VOLTAGE = 3.0; // Minimum safe discharge voltage
  const float MAX_VOLTAGE = 4.2; // Fully charged voltage

  // Check if the voltage is within the expected range
  if (voltage >= MAX_VOLTAGE) {
    return 100.0;
  } else if (voltage <= MIN_VOLTAGE) {
    return 0.0;
  } else {
    // Perform a linear interpolation
    // Formula: ((voltage - min) / (max - min)) * 100
    float percentage = ((voltage - MIN_VOLTAGE) / (MAX_VOLTAGE - MIN_VOLTAGE)) * 100.0;
    return percentage;
  }
}

void sendHealthAndRSSI(int rssi) {
    StaticJsonDocument<200> doc;
    doc["rssi"] = rssi;
    // doc["battery_v"] = voltage;
    doc["uptime"] = millis() / 1000;

    uint32_t Vbatt = 0;
    for(int i = 0; i < 16; i++) {
      Vbatt += analogReadMilliVolts(A0); // Read and accumulate ADC voltage
    }
    float Vbattf = 2 * Vbatt / 16 / 1000.0;     // Adjust for 1:2 divider and convert to volts
    float vBatPercent = voltageToPercentage(Vbattf);
    Serial.print(F("Battery voltage: "));
    Serial.println(Vbattf, 3);                  // Output voltage to 3 decimal places
    Serial.print(F("Battery Percent:"));
    Serial.println(vBatPercent);
    doc["battery_percentage"] = vBatPercent;


    char buffer[200];
    serializeJson(doc, buffer);
    mqttClient.publish("workroom/ble_scanner/esp_c6_leds/attributes", buffer);
}