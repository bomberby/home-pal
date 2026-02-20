#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ESPmDNS.h>
#include <ArduinoJson.h>
#include "Zigbee.h"
#include <time.h>

#include "secrets.h"
#include "display.h"
#include "zigbee_reporter.h"
#include "metrics.h"

// For measurments:
#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME280.h>
#include <SensirionI2CSgp41.h>
#include <VOCGasIndexAlgorithm.h>
#include <NOXGasIndexAlgorithm.h>
#include <Preferences.h>

// Due to the size of using zigbee+wifi+display adapter, need to use minimal board partition size
// Example: Create partitions.csv with the following content:
// # Name,   Type, SubType, Offset,  Size, Flags
// nvs,      data, nvs,     0x9000,  0x4000,
// otadata,  data, ota,     0xd000,  0x2000,
// phy_init, data, phy,     0xf000,  0x1000,
// factory,  app,  factory, 0x10000,  2M,
// zb_storage,data, fat,    ,        0x4000,
// zb_fct,   data, fat,     ,        0x1000,
// spiffs,   data, spiffs,  ,        0x10000,

// --- WiFi & API ---
const char* url_host = "desktop-necq7ps";
const char* url_port = "5000";

// const char* serverDeviceStatus = "http://192.168.1.232:5000/sh/weather_dashboard";
const char* serverDevicePath = "/sh/weather_dashboard";

// const char* serverWeatherUrl = "http://192.168.1.232:5000/weather";
const char* serverWeatherPath = "/weather";
const char* serverImagePath = "/image.bin";
#define TIME_TO_SLEEP  30        // Minutes to sleep
#define S_TO_uS_FACTOR 60000000ULL // Conversion factor for minutes to microseconds

#ifndef ZIGBEE_MODE_ED
#error "Zigbee end device mode is not selected in Tools->Zigbee mode"
#endif

// Sensor configurations
#define I2C_SDA 7
#define I2C_SCL 6
Adafruit_BME280 bme;
SensirionI2CSgp41 sgp41;
VOCGasIndexAlgorithm voc_algo;
NOxGasIndexAlgorithm nox_algo;
Preferences preferences;

SystemMetrics currentMetrics;
void performAirQualityMeasurement(int32_t &vocIndex, int32_t &noxIndex);


void setup() {
  Serial.begin(115200);

  // INDICATE AWAKE STATUS
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, HIGH); // LED ON

  storeBatteryMeasurment();


  Wire.begin(I2C_SDA, I2C_SCL);
  if (!bme.begin(0x76, &Wire)) { 
    Serial.println("Could not find sensor! Check wiring/address.");
  }
  currentMetrics.temperature = bme.readTemperature();
  currentMetrics.humidity = bme.readHumidity();
  currentMetrics.pressure = bme.readPressure() / 100.0F;
  currentMetrics.altitude = bme.readAltitude(1013.25);

  // SGP logic
  float voc = 0;
  float nox = 0;
  performAirQualityMeasurement(voc, nox);

  // Results are now available for MQTT (our next step)
  Serial.printf("\nFinal Reported VOC: %.2f\n", voc);
  Serial.printf("Final Reported NOx: %.2f\n", nox);

  if (currentMetrics.batteryPercent < 5 && !currentMetrics.externalPower) {
      Serial.println(F("BATTERY CRITICAL. Skipping WiFi/Screen."));
      // report the emergency status, then sleep
    } else {  
    // Connect WiFi & Sync Time
    WiFi.begin(ssid, password);
    int attempt = 0;
    while (WiFi.status() != WL_CONNECTED && attempt < 20) {
      delay(500);
      attempt++;
    }
    
    if (WiFi.status() == WL_CONNECTED) {
      configTime(32400, 0, "pool.ntp.org"); // UTC+9 for Japan
      struct tm timeinfo;
      getLocalTime(&timeinfo); // Sync internal clock

      // SNAPSHOT RSSI while WiFi is active
      currentMetrics.wifiRssi = WiFi.RSSI();

      // 2. Fetch and Render
      performUpdate();

      // 3. Prepare Display for Sleep
      hibernateDisplay(); 
    }
    else {
      currentMetrics.wifiRssi = -100.0;
    }

    Serial.println(F("Shutting down WiFi..."));
    WiFi.disconnect(true);
    WiFi.mode(WIFI_OFF);
    delay(100); // Let the radio stabilize
  }

  Serial.println(F("Starting Zigbee Report..."));
  sendZigbeeReport();

  // 4. Set Sleep Timer and Enter Deep Sleep
  Serial.println("Entering Deep Sleep...");
  digitalWrite(LED_BUILTIN, LOW); // LED OFF - Sleep starting
  esp_sleep_enable_timer_wakeup(TIME_TO_SLEEP * S_TO_uS_FACTOR);
  esp_deep_sleep_start();
}

void loop() {}

void performUpdate() {
  HTTPClient http;
  IPAddress serverIP;
  String mode = "weather"; // Default

  if (MDNS.begin("esp32-led")) { // Initialize mDNS
    serverIP = MDNS.queryHost(url_host); // Note: No ".local" here
  }
  String url = "http://" + serverIP.toString() + ":" + url_port + serverDevicePath;
  http.begin(url);
  int httpCode = http.GET();
  if (httpCode == HTTP_CODE_OK) {
    String payload = http.getString();
    DynamicJsonDocument doc(24000); 
    DeserializationError error = deserializeJson(doc, payload);
    if (!error) {
      JsonObject root = doc.as<JsonObject>();
      // TODO: do not render any display if turned off
      // root["activated"]
      mode = (String)root["mode"];
      std::transform(mode.begin(), mode.end(), mode.begin(), ::tolower);
      Serial.print(F("Set display mode to:"));
      Serial.println(mode);
    }
  }
  http.end();

  if (mode == "photos") {
    String image_url = "http://" + serverIP.toString() + ":" + url_port + serverImagePath;
    drawRemoteImage(image_url);
  } else if (mode == "weather") {
    performWeatherUpdate();
  } else {
    performWeatherUpdate();
  }
}

void performWeatherUpdate() {
  HTTPClient http;
  IPAddress serverIP;

  if (MDNS.begin("esp32-led")) { // Initialize mDNS
    serverIP = MDNS.queryHost(url_host); // Note: No ".local" here
  }
  String url = "http://" + serverIP.toString() + ":" + url_port + serverWeatherPath;
  http.begin(url);
  int httpCode = http.GET();

  if (httpCode == HTTP_CODE_OK) {
    String payload = http.getString();
    DynamicJsonDocument doc(24000); 
    DeserializationError error = deserializeJson(doc, payload);

    if (!error) {
      JsonObject root = doc.as<JsonObject>();
      JsonObject cityData;
      String cityName = "";

      for (JsonPair kv : root) {
        cityName = kv.key().c_str();
        cityData = kv.value().as<JsonObject>();
        break; 
      }

      const char* firstTimeStr = cityData["first_time"];
      const char* lastUpdateStr = cityData["last_updated"];
      float temps[168], precips[168];
      
      int tCount = parseJsonArrayString(cityData["hourly_temperatures"], temps, 168);
      int pCount = parseJsonArrayString(cityData["hourly_precipitation"], precips, 168);

      renderWeather(cityName.c_str(), firstTimeStr, lastUpdateStr, temps, precips, tCount);
    }
  }
  http.end();
}

int parseJsonArrayString(const char* input, float* output, int maxLen) {
  String data = String(input);
  data.replace("[", ""); data.replace("]", "");
  int found = 0;
  int startIdx = 0;
  int commaIdx = data.indexOf(',');
  
  while (commaIdx != -1 && found < maxLen) {
    output[found++] = data.substring(startIdx, commaIdx).toFloat();
    startIdx = commaIdx + 1;
    commaIdx = data.indexOf(',', startIdx);
  }
  if (found < maxLen) output[found++] = data.substring(startIdx).toFloat();
  return found;
}

void drawRemoteImage(String url) {
  HTTPClient http;
  http.begin(url);
  
  int httpCode = http.GET();
  if (httpCode == HTTP_CODE_OK) {
    Serial.println(F("Getting image stream"));
    int len = http.getSize(); // Should be 192,000 bytes for 800x480 color, or 
    WiFiClient* stream = http.getStreamPtr();

    
    // Allocate a temporary buffer for the image
    // 192KB fits in C6 RAM, but we use psram if available or static allocation
    uint8_t* imageBuffer = (uint8_t*)malloc(len);
    
    if (imageBuffer) {

      int bytesRead = stream->readBytes(imageBuffer, len);
      
      if (bytesRead == len) {
        Serial.println("Commencing rendering of image:");
        Serial.println(len);
        if (len != 192000){ // 800*480 BW should be 48,062, 1/8 the size +62 for headers etc
          renderBwFromBuffer(imageBuffer);
        } else { // Color probably with 192,000
        // renderFromBuffer(imageBuffer);
          renderFromBuffer(imageBuffer, len);
        }
      }
      free(imageBuffer);
    }
  }
  http.end();
}


void storeBatteryMeasurment()
{
  bool usbDataConnected = usb_serial_jtag_is_connected();
  currentMetrics.externalPower = usbDataConnected;

  uint32_t raw = 0;
  for(int i=0; i<15; i++) raw += analogReadMilliVolts(0); // More samples for a better baseline
  raw /= 15;
  // currentMetrics.batteryVoltage = (raw / 4095.0) * 3.3 * 2.0;
  currentMetrics.batteryVoltage = (raw * 2.0) / 1000.0;
  // currentMetrics.batteryVoltage = raw * 2.0;

  bool isExternalPower = usbDataConnected || (currentMetrics.batteryVoltage < 3.0) || (currentMetrics.batteryVoltage > 4.25);  
  if (isExternalPower) {
    currentMetrics.batteryPercent = 101; // Special flag for "External Power"
  } else {
    currentMetrics.batteryPercent = constrain((int)((currentMetrics.batteryVoltage - 3.3) * 100 / (4.2 - 3.3)), 0, 100);
  }
  Serial.print("Raw Number: ");
  Serial.print(raw);
  Serial.print("Voltage: ");
  Serial.print(currentMetrics.batteryVoltage);
  Serial.print("V - Percentage: ");
  Serial.print(currentMetrics.batteryPercent);
  Serial.println("%");
}

/**
 * Handles initialization, stabilization, and reading of SGP41.
 */
void performAirQualityMeasurement(float &vocResult, float &noxResult) {
    sgp41.begin(Wire);

    // preferences.clear();
    // Use stored data
    preferences.begin("sgp_raw", false);

    // 1. Get our own Baseline from NVM
    float baseVoc = preferences.getFloat("base_voc", 30000.0);
    float baseNox = preferences.getFloat("base_nox", 18000.0);
    Serial.printf("last raw: VOC: %.0f, NOx: %.0f \n", baseVoc, baseNox);

    uint16_t srawVoc, srawNox;
    uint32_t sumVoc = 0;
    uint32_t sumNox = 0;
    int measurementCount = 0;
    uint16_t humTicks = (uint16_t)(currentMetrics.humidity * 65535 / 100);
    uint16_t tempTicks = (uint16_t)((currentMetrics.temperature + 45) * 65535 / 175);

    for (int i = 0; i < 20; i++) {
      
      if (i < 5) {
        // Phase 1: Conditioning (5s)
        sgp41.executeConditioning(humTicks, tempTicks, srawVoc);
      } else {
        // Phase 2: Measuring (15s)
        if (!sgp41.measureRawSignals(humTicks, tempTicks, srawVoc, srawNox)) {
          // SCRAPE: Only add to the average if we are past the 8th second total
          // This ignores the first 3 seconds of thermal drift
          if (i >= 8) { 
            sumVoc += srawVoc;
            sumNox += srawNox;
            measurementCount++;
          }
        }
      }
      if (i % 5 == 0 || i == 19) {
          Serial.printf("[%ds] RawVOC: %u | RawNox: %u\n", i, srawVoc, srawNox);
      }
      delay(1000);
    }
    if (measurementCount > 0) {
      // Calculate the average of the samples
      float avgVoc = (float)sumVoc / measurementCount;
      float avgNox = (float)sumNox / measurementCount;

      // 1. Calculate the final results to be sent via MQTT
      vocResult = 100.0 + (baseVoc - avgVoc) / 18.0; 
      noxResult = 1.0 + (avgNox - baseNox) / 10.0;

      // 2. Update the Weighted Baseline (EMA) using the average
      // This makes the "memory" of the sensor much more robust
      float newBaseVoc = (baseVoc * 0.9) + (avgVoc * 0.1);
      float newBaseNox;
      if (noxResult < 0) { // Be more quick to set to a new "cleanest air"
        newBaseNox = (baseNox * 0.7) + (avgNox * 0.3);
      } else {
        newBaseNox = (baseNox * 0.9) + (avgNox * 0.1);
      }


      preferences.putFloat("base_voc", newBaseVoc);
      preferences.putFloat("base_nox", newBaseNox);

      currentMetrics.vocIndex = vocResult;
      currentMetrics.noxIndex = noxResult;
      currentMetrics.vocRaw = avgVoc;
      currentMetrics.noxRaw = avgNox;

      Serial.printf("Final Average VOC Raw: %.2f | VOC Index: %.2f\n", avgVoc, vocResult);
      Serial.printf("Final Average NOX Raw: %.2f | NOX Index: %.2f\n", avgNox, noxResult);
    }


    preferences.end();

    // Power down heater to preserve the sensor since we are done
    sgp41.turnHeaterOff();
    Serial.println(" Measurement complete.");
}