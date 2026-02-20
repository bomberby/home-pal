#include "zigbee_reporter.h"
#include <Arduino.h>
#include "Zigbee.h"


#include "metrics.h"

// Endpoint 10: Primary Weather Data
ZigbeeTempSensor zbTemp = ZigbeeTempSensor(10);

// Endpoint 11: WiFi Signal Strength (Generic Analog)
ZigbeeAnalog zbWiFiSignal = ZigbeeAnalog(11);

// Endpoint 12: Pressure sensors
ZigbeePressureSensor zbPressureSensor = ZigbeePressureSensor(12);

ZigbeeAnalog vocSensor = ZigbeeAnalog(13);
ZigbeeAnalog noxSensor = ZigbeeAnalog(14);


void sendZigbeeReport() {
  // DEBUG
  // currentMetrics.batteryVoltage = 3.7;
  // currentMetrics.batteryPercent = 50;

  const zb_power_source_t powerSource = (currentMetrics.batteryPercent > 100) ? ZB_POWER_SOURCE_MAINS : ZB_POWER_SOURCE_BATTERY;
  const uint8_t zbPercent = (uint8_t)(constrain(currentMetrics.batteryPercent, 0, 99));
  const uint8_t zbVoltage = (uint8_t)(currentMetrics.batteryVoltage * 10);
  bool softReboot = esp_sleep_get_wakeup_cause() == ESP_SLEEP_WAKEUP_TIMER;
  Serial.println(F("--- INITIATING ZIGBEE TELEMETRY ---"));
  

  
  zbTemp.setManufacturerAndModel(MANUFACTURER_NAME, MODEL_ID);
  bool powerSet = zbTemp.setPowerSource(powerSource, zbPercent, zbVoltage);
  zbTemp.addHumiditySensor(0,100,0.5);
  Zigbee.addEndpoint(&zbTemp);
  buildWifiReport();

  zbPressureSensor.setManufacturerAndModel(MANUFACTURER_NAME, MODEL_ID);
  Zigbee.addEndpoint(&zbPressureSensor);

  vocSensor.addAnalogInput();
  noxSensor.addAnalogInput();
  vocSensor.setAnalogInputDescription("VOC Index");
  noxSensor.setAnalogInputDescription("NOx Index");
  vocSensor.setAnalogInputApplication(ESP_ZB_ZCL_AI_APP_TYPE_OTHER);
  noxSensor.setAnalogInputApplication(ESP_ZB_ZCL_AI_APP_TYPE_OTHER);
  vocSensor.setAnalogInputResolution(0.01);
  noxSensor.setAnalogInputResolution(0.01);

  Zigbee.addEndpoint(&vocSensor);
  Zigbee.addEndpoint(&noxSensor);

  bool factoryReset = false;

  if (!Zigbee.begin(ZIGBEE_END_DEVICE, factoryReset)) {
    Serial.println(F("Zigbee failed to start!"));
    return;
  }

  Serial.print(F("Connecting to Zigbee Network..."));
  unsigned long startAttempt = millis();
  while (!Zigbee.connected() && millis() - startAttempt < 15000) {
    Serial.print(".");
    delay(200);
  }

  zbTemp.setHumidity(currentMetrics.humidity);
  zbTemp.setTemperature(currentMetrics.temperature);
  zbPressureSensor.setPressure(currentMetrics.pressure);
  vocSensor.setAnalogInputReporting(1, 60, 0.5);
  noxSensor.setAnalogInputReporting(1, 60, 0.5);

  Serial.println();
  if (Zigbee.connected()) {
    delay(400);

    if (powerSet && powerSource == ZB_POWER_SOURCE_BATTERY) {
      Serial.println(F("Setting power information"));
      // IMPORTANT: setting battery information when reporting as non-battery will raise exceptions
      zbTemp.setBatteryPercentage(zbPercent);
      zbTemp.setBatteryVoltage(zbVoltage);
      // zbTemp.reportBatteryPercentage();  // causes crash loop on assertion
    }

    Serial.println(F("[SUCCESS] Connected to Coordinator."));
    if (Zigbee.started()) {
      zbWiFiSignal.setAnalogInput((float)currentMetrics.wifiRssi);
      vocSensor.setAnalogInput(currentMetrics.vocIndex);
      noxSensor.setAnalogInput(currentMetrics.noxIndex);
    }

    // If woke up due to reset or flashing, give extra time to reconfigure zigbee network
    if (!softReboot) {
      Serial.println(F("Reconfigure if needed now"));
      delay(30000);
    }

    // Manually trigger a report before going to sleep
    if (zbTemp.report()){
      Serial.println(F("Successfully reported"));
    } else
    {
      Serial.println(F("Error while reporting to coordinator"));
    }
  } else {
    Serial.println(F("\n[TIMEOUT] Could not find Zigbee network."));
  }


  Serial.println(F("Zigbee Dispatch in progress."));
  delay(15000);
  Serial.println(F("Zigbee Dispatch complete."));
}

void buildWifiReport(){
  analogReadResolution(10);

  zbWiFiSignal.setManufacturerAndModel(MANUFACTURER_NAME, MODEL_ID);

  if (zbWiFiSignal.addAnalogInput()) {
    Serial.println(F("\nReporting zbWiFiSignal measurements."));
    zbWiFiSignal.setAnalogInputMinMax(-100.0, 0.0);
    zbWiFiSignal.setAnalogInputDescription("WiFi RSSI");
    zbWiFiSignal.setAnalogInputApplication(ESP_ZB_ZCL_AI_APP_TYPE_OTHER);
    zbWiFiSignal.setAnalogInputResolution(0.1);
    Zigbee.addEndpoint(&zbWiFiSignal);
  } else {
    Serial.println("Failed to add zbWiFiSignal to Analog Input cluster!");
  }
}