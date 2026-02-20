#include <WiFi.h>
#include <HTTPClient.h>
#include <ESPmDNS.h>
#include <ArduinoJson.h>
#include <Adafruit_NeoPixel.h>
#include <freertos/semphr.h>
#include "secrets.h"
#include <PubSubClient.h>
#include "presence_task.h"

// ------------------- LED SETUP -------------------
#define LED_PIN     2
#define NUM_LEDS    60
Adafruit_NeoPixel strip(NUM_LEDS, LED_PIN, NEO_GRBW + NEO_KHZ800);

// ------------------- WIFI INFO -------------------

WiFiClient espClient;
PubSubClient mqttClient(espClient);
// const char* url = "http://192.168.1.232:5000/sh/led";
// const char* url = "http://desktop-necq7ps.local:5000/sh/led";
const char* url_host = "desktop-necq7ps";
const char* url_path = "/sh/led";
const char* url_port = "5000";
const char* mqtt_server = "homeassistant.local"; // Your Home Assistant IP address

// Animation state arrays
unsigned long lastAnimTime[NUM_LEDS] = {0};
bool flashState[NUM_LEDS] = {false};
float pulsePhase[NUM_LEDS] = {0}; // for simple pulse animations

StaticJsonDocument<4096> sharedDoc;  // store the latest indicator JSON
SemaphoreHandle_t ledDocMutex;       // protects sharedDoc
TaskHandle_t ledTaskHandle = nullptr; // handle to the LED task

void setup() {
  Serial.begin(115200);

  strip.begin();
  strip.setBrightness(50);
  strip.show(); // all off

  pinMode(LED_BUILTIN, OUTPUT); // Debug led

  ledDocMutex = xSemaphoreCreateMutex();

  // ---- CONNECT TO WIFI ----
  Serial.println("Connecting to WiFi...");
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  while (WiFi.status() != WL_CONNECTED) {
    delay(300);
    Serial.print(".");
  }
  Serial.println("\nConnected!");
  mqttClient.setServer(mqtt_server, 1883);
  setupBLETracker(BLE_UUID);
}

void loop() {
  if (WiFi.status() == WL_CONNECTED) {
    HTTPClient http;
    IPAddress serverIP;

    digitalWrite(LED_BUILTIN, HIGH);
    if (MDNS.begin("esp32-led")) { // Initialize mDNS
      serverIP = MDNS.queryHost(url_host); // Note: No ".local" here
    }
    String url = "http://" + serverIP.toString() + ":" + url_port + url_path;
    http.begin(url);
    int httpCode = http.GET();
    digitalWrite(LED_BUILTIN, LOW);  

    if (httpCode == 200) {   // OK
      String payload = http.getString();
      // Serial.println("Received:");
      // Serial.println(payload);

      // ---- PARSE JSON ----
      StaticJsonDocument<4096> doc;
      DeserializationError error = deserializeJson(doc, payload);

      if (!error) {
        bool ledState = doc["activated"];   // true or false

        if (ledState) {
          String mode = doc["mode"];
          if (mode=="rainbow") {
            startRainbow();
            stopIndicatorModeTask();
          }
          else {
            stopRainbow();
            if (mode=="indicator_mode") {
              startIndicatorModeTask(doc);
            } else {
              stopIndicatorModeTask();
              turnOnLEDs();
            }
          }
        } else {
          turnOffLEDs();
          stopRainbow();
          stopIndicatorModeTask();
        }
      }
      else {
        Serial.println("JSON parsing failed!");
      }
    }
    else {
      Serial.print("HTTP Error: ");
      Serial.println(httpCode);
    }

    if (!mqttClient.connected()) {
      reconnectMQTT();
    }
    mqttClient.loop();


    http.end();
  }

  delay(5000); // Check every 5 seconds
}

// ------------------- LED CONTROL -------------------

void turnOnLEDs() {
  // Example: turn strip bright white
  for (int i = 0; i < NUM_LEDS; i++) {
    strip.setPixelColor(i, strip.Color(0, 0, 0, 255)); // Use W channel
  }
  strip.show();
}

TaskHandle_t rainbowTaskHandle = NULL;
volatile bool rainbowStopRequested = false;

void startRainbow() {
  if (rainbowTaskHandle != NULL) {
    // Serial.println("Rainbow already running.");
    return;
  }
  rainbowStopRequested = false;
  // testTask();
  Serial.print("Starting task");
  xTaskCreatePinnedToCore(
    rainbowTask,
    "RainbowTask",
    4096,
    NULL,
    1,
    &rainbowTaskHandle,
    0   // run on core 0
  );
}

void stopRainbow() {
  if (rainbowTaskHandle == NULL) return;

  rainbowStopRequested = true;

  // Wait for task to delete itself
  while (rainbowTaskHandle != NULL) {
    delay(10);
  }

  Serial.println("Rainbow task stopped.");
}



void rainbowTask(void *parameter) {
  return TurnRainbow();
  rainbowStopRequested = false;

  Serial.println("Rainbow task started.");

  TurnRainbow();   // <-- Your original blocking function

  Serial.println("Rainbow task finished.");

  rainbowTaskHandle = NULL;
  vTaskDelete(NULL);   // Self-delete
}

void TurnRainbow() {
  int wait = 5;
  while (1) {
    for(long firstPixelHue = 0; firstPixelHue < 5*65536; firstPixelHue += 256) {
      if (rainbowStopRequested) {
        Serial.println("Rainbow task finished, destroying.");
        rainbowTaskHandle = NULL;
        vTaskDelete(NULL);
        return; // thread to be killed
      }
      for(int i=0; i<strip.numPixels(); i++) {
        int pixelHue = firstPixelHue + (i * 65536L / strip.numPixels());
        strip.setPixelColor(i, strip.gamma32(strip.ColorHSV(pixelHue)));
      }
      strip.show();
      vTaskDelay(wait);
    }
  }
}



void ledTask(void* pvParameters) {
    const TickType_t delayTicks = pdMS_TO_TICKS(40); // update every 40ms

    for (;;) {
        if (xSemaphoreTake(ledDocMutex, portMAX_DELAY) == pdTRUE) {
            handleIndicatorMode(sharedDoc); // call your method
            xSemaphoreGive(ledDocMutex);
        }
        vTaskDelay(delayTicks);
    }
}


void startIndicatorModeTask(const JsonDocument& doc) {
    if (xSemaphoreTake(ledDocMutex, portMAX_DELAY) == pdTRUE) {
        sharedDoc.clear();
        sharedDoc.set(doc.as<JsonVariantConst>()); 
        xSemaphoreGive(ledDocMutex);
    }

    if (ledTaskHandle == nullptr) {
        xTaskCreate(
            ledTask,
            "LED Task",
            4096,
            nullptr,
            1,
            &ledTaskHandle
        );
    }
}

void stopIndicatorModeTask() {
    if (ledTaskHandle != nullptr) {
        vTaskDelete(ledTaskHandle);
        ledTaskHandle = nullptr;

        // Optionally turn off LEDs when stopping
        strip.clear();
        strip.show();
    }
}




// Call this method when mode == "indicator_mode"
void handleIndicatorMode(JsonDocument& doc) {
    bool activated = doc["activated"] | false;

    JsonArray indicators = doc["indicators"];

    for (int i = 0; i < NUM_LEDS; i++) {
    strip.setPixelColor(i, 0);
    }

    for (JsonObject indicator : indicators) {
        JsonArray leds = indicator["leds"];
        JsonArray indicatorAnimations = indicator["animations"];

        for (JsonObject led : leds) {
            int index = led["index"];
            if (index < 0 || index >= NUM_LEDS) continue;

            // ---- Base color ----
            int r = led["color"][0];
            int g = led["color"][1];
            int b = led["color"][2];
            int brightness = led["brightness"] | 255;

            float final_r = (r * brightness) / 255;
            float final_g = (g * brightness) / 255;
            float final_b = (b * brightness) / 255;

            // ---- Apply indicator-level animations ----
            applyAnimations(
                indicatorAnimations,
                index,
                final_r, final_g, final_b
            );

            // ---- Apply LED-level animations (override layer) ----
            JsonArray ledAnimations = led["animations"];
            applyAnimations(
                ledAnimations,
                index,
                final_r, final_g, final_b
            );

            strip.setPixelColor(
                index,
                strip.Color(
                    (int)final_r,
                    (int)final_g,
                    (int)final_b
                )
            );
        }
    }

    strip.show();
}


void applyAnimations(
    JsonArray animations,
    int index,
    float &r, float &g, float &b
) {
    if (animations.isNull()) return;

    unsigned long now = millis();

    for (JsonObject anim : animations) {
        const char* type = anim["type"];
        int ar = anim["color"][0];
        int ag = anim["color"][1];
        int ab = anim["color"][2];
        int brightness = anim["brightness"] | 255;
        int interval = anim["interval_ms"] | 1000;

        if (strcmp(type, "flash") == 0) {
            if (now - lastAnimTime[index] >= interval) {
                flashState[index] = !flashState[index];
                lastAnimTime[index] = now;
            }

            if (flashState[index]) {
                r = (ar * brightness) / 255;
                g = (ag * brightness) / 255;
                b = (ab * brightness) / 255;
            } else {
                r = g = b = 0;
            }
        }
        else if (strcmp(type, "pulse") == 0) {
            pulsePhase[index] += 0.08f;
            if (pulsePhase[index] > TWO_PI) {
                pulsePhase[index] -= TWO_PI;
            }

            float factor = (sin(pulsePhase[index]) + 1.0f) / 2.0f;

            int pr = (ar * brightness * factor) / 255;
            int pg = (ag * brightness * factor) / 255;
            int pb = (ab * brightness * factor) / 255;

            // Blend via mean
            r = (r + pr) / 2;
            g = (g + pg) / 2;
            b = (b + pb) / 2;
        }
    }
}

void turnOffLEDs() {
  strip.clear();
  for (int i = 0; i < NUM_LEDS; i++) {
    strip.setPixelColor(i, 0); // Off
  }
  strip.show();
}



void reconnectMQTT() {
  // Loop until we're reconnected
  while (!mqttClient.connected()) {
    Serial.print("Attempting MQTT connection...");
    // Create a random client ID
    String clientId = "ESP32C6Client-";
    clientId += String(random(0xffff), HEX);
    
    // Attempt to connect
    if (mqttClient.connect(clientId.c_str(),MQTT_USER,MQTT_PASSWORD)) {
      Serial.println("connected");
    } else {
      Serial.print("failed, rc=");
      Serial.print(mqttClient.state());
      Serial.println(" try again in 5 seconds");
      vTaskDelay(5000 / portTICK_PERIOD_MS);
    }
  }
}