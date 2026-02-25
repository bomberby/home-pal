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
const char* url_path = "/sh/led";

// Animation state arrays
unsigned long lastAnimTime[NUM_LEDS] = {0};
bool flashState[NUM_LEDS] = {false};
float pulsePhase[NUM_LEDS] = {0}; // for simple pulse animations
int cometPos[NUM_LEDS] = {0};    // comet head position per indicator key

StaticJsonDocument<4096> sharedDoc;  // store the latest indicator JSON
SemaphoreHandle_t ledDocMutex;       // protects sharedDoc
SemaphoreHandle_t mqttMutex;         // protects mqttClient across main loop and BLE task
TaskHandle_t ledTaskHandle = nullptr;
volatile bool ledTaskStopRequested = false;

IPAddress serverIP;  // cached — resolved once, re-resolved only on failure
volatile unsigned long serverUnreachableSince = 0;  // millis() of first consecutive failure; 0 = OK
#define SERVER_UNREACHABLE_THRESHOLD_MS 60000       // 1 minute before showing indicator

bool resolveServer() {
  serverIP = MDNS.queryHost(SERVER_HOST);
  if (serverIP == INADDR_NONE) {
    Serial.printf("[mDNS] Could not resolve %s\n", SERVER_HOST);
    return false;
  }
  Serial.printf("[mDNS] %s -> %s\n", SERVER_HOST, serverIP.toString().c_str());
  return true;
}

void setup() {
  Serial.begin(115200);

  strip.begin();
  strip.setBrightness(50);
  strip.show(); // all off

  pinMode(LED_BUILTIN, OUTPUT); // Debug led

  ledDocMutex = xSemaphoreCreateMutex();
  mqttMutex   = xSemaphoreCreateMutex();

  // ---- CONNECT TO WIFI ----
  Serial.println("Connecting to WiFi...");
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  unsigned long wifiStart = millis();
  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - wifiStart > 20000) {
      Serial.println("\n[WiFi] Timed out — restarting");
      ESP.restart();
    }
    delay(300);
    Serial.print(".");
  }
  Serial.println("\nConnected!");
  MDNS.begin("esp32-led");
  resolveServer();
  mqttClient.setServer(MQTT_SERVER, 1883);
  setupBLETracker(BLE_UUID);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WiFi] Disconnected — reconnecting...");
    WiFi.reconnect();
    delay(5000);
    if (WiFi.status() != WL_CONNECTED) return;
    Serial.println("[WiFi] Reconnected");
    MDNS.begin("esp32-led");  // restart mDNS after reconnect
    serverIP = INADDR_NONE;   // force re-resolve
  }

  if (serverIP == INADDR_NONE && !resolveServer()) {
    // Can't reach server — mark as unreachable and skip HTTP this cycle
    if (serverUnreachableSince == 0) serverUnreachableSince = millis();
    pumpMQTT(5000);
    return;
  }

  {
    HTTPClient http;
    http.setTimeout(3000);
    digitalWrite(LED_BUILTIN, HIGH);
    String url = "http://" + serverIP.toString() + ":" + SERVER_PORT + url_path;
    http.begin(url);
    int httpCode = http.GET();
    digitalWrite(LED_BUILTIN, LOW);

    if (httpCode == 200) {   // OK
      serverUnreachableSince = 0;  // server is reachable — reset error window
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
            stopIndicatorModeTask();
            startRainbow();
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
      Serial.printf("[HTTP] Error %d — clearing cached IP for re-resolve\n", httpCode);
      serverIP = INADDR_NONE;
      if (serverUnreachableSince == 0) serverUnreachableSince = millis();
    }

    http.end();
  }

  pumpMQTT(5000);  // wait for next poll cycle, keeping MQTT alive
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
  TurnRainbow();  // exits only via vTaskDelete(NULL) inside TurnRainbow
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
            handleIndicatorMode(sharedDoc);
            xSemaphoreGive(ledDocMutex);
        }
        if (ledTaskStopRequested) {
            ledTaskStopRequested = false;
            ledTaskHandle = nullptr;
            vTaskDelete(NULL);  // self-delete — mutex is already released at this point
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
    if (ledTaskHandle == nullptr) return;
    ledTaskStopRequested = true;
    while (ledTaskHandle != nullptr) delay(10);  // wait for task to self-delete
    strip.clear();
    strip.show();
}




// Call this method when mode == "indicator_mode"
void handleIndicatorMode(JsonDocument& doc) {
    JsonArray indicators = doc["indicators"];

    for (int i = 0; i < NUM_LEDS; i++) {
    strip.setPixelColor(i, 0);
    }

    for (JsonObject indicator : indicators) {
        JsonArray leds = indicator["leds"];
        JsonArray indicatorAnimations = indicator["animations"];

        // Dispatch comet or standard per-LED rendering
        JsonObject cometAnim;
        bool hasComet = false;
        if (!indicatorAnimations.isNull()) {
            for (JsonObject anim : indicatorAnimations) {
                const char* animType = anim["type"] | "";
                if (strcmp(animType, "comet") == 0) {
                    cometAnim = anim;
                    hasComet = true;
                    break;
                }
            }
        }

        if (hasComet) {
            int keyIndex = -1;
            for (JsonObject led : leds) { keyIndex = led["index"] | -1; break; }
            if (keyIndex >= 0) applyComet(leds, keyIndex, cometAnim);
        } else {
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
    }

    // Status LEDs — composited last so they're never overwritten by indicator data
    // LED 56: WiFi / server reachability
    if (WiFi.status() != WL_CONNECTED) {
        float factor = (sin(millis() / 1000.0f) + 1.0f) / 2.0f;  // 1-second pulse
        strip.setPixelColor(56, strip.Color((int)(factor * 200), 0, 0));  // red pulse: no WiFi
    } else if (serverUnreachableSince != 0 &&
               millis() - serverUnreachableSince >= SERVER_UNREACHABLE_THRESHOLD_MS) {
        float factor = (sin(millis() / 2000.0f) + 1.0f) / 2.0f;  // 2-second pulse
        strip.setPixelColor(56, strip.Color((int)(factor * 220), (int)(factor * 80), 0));  // orange pulse: server down
    } else {
        strip.setPixelColor(56, strip.Color(0, 0, 15));  // dim blue: all OK
    }
    // LED 58: MQTT connection (dim blue = connected, dim amber = disconnected)
    strip.setPixelColor(58, mqttClient.connected() ? strip.Color(0, 0, 15) : strip.Color(20, 8, 0));

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
        const char* type = anim["type"] | "";
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
            // Advance phase by one frame's worth of the requested period.
            // ledTask runs every 40ms, so increment = (2π × 40) / interval_ms
            pulsePhase[index] += (TWO_PI * 40.0f) / (float)interval;
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

void applyComet(JsonArray leds, int keyIndex, JsonObject anim) {
    int ar       = anim["color"][0] | 255;
    int ag       = anim["color"][1] | 240;
    int ab       = anim["color"][2] | 200;
    int brightness = anim["brightness"] | 255;
    int tailLen  = anim["tail_length"] | 3;
    int interval = anim["interval_ms"] | 100;

    int ledCount = leds.size();
    if (ledCount == 0) return;

    int total = ledCount + tailLen;  // comet cycles through this range

    unsigned long now = millis();
    if (now - lastAnimTime[keyIndex] >= (unsigned long)interval) {
        cometPos[keyIndex]++;
        if (cometPos[keyIndex] >= total) cometPos[keyIndex] = 0;
        lastAnimTime[keyIndex] = now;
    }

    int head = cometPos[keyIndex];

    int ledIdx = 0;
    for (JsonObject led : leds) {
        int index = led["index"] | -1;
        if (index < 0 || index >= NUM_LEDS) { ledIdx++; continue; }

        int r = led["color"][0];
        int g = led["color"][1];
        int b = led["color"][2];
        int ledBrightness = led["brightness"] | 255;

        float fr = (r * ledBrightness) / 255.0f;
        float fg = (g * ledBrightness) / 255.0f;
        float fb = (b * ledBrightness) / 255.0f;

        // head at ledIdx means this LED is the comet head;
        // Apply per-LED animations (e.g. flash) before comet overlay
        JsonArray ledAnimations = led["animations"];
        applyAnimations(ledAnimations, index, fr, fg, fb);

        // dist > 0 means the head has passed this LED (tail)
        int dist = head - ledIdx;
        if (dist >= 0 && dist <= tailLen) {
            float factor = 1.0f - (float)dist / (tailLen + 1);
            fr = min(255.0f, fr + (ar * brightness * factor) / 255.0f);
            fg = min(255.0f, fg + (ag * brightness * factor) / 255.0f);
            fb = min(255.0f, fb + (ab * brightness * factor) / 255.0f);
        }

        strip.setPixelColor(index, strip.Color((int)fr, (int)fg, (int)fb));
        ledIdx++;
    }
}

void turnOffLEDs() {
  strip.clear();
  strip.show();
}



// Pump MQTT for durationMs, yielding every 500ms so the BLE task can publish.
// reconnectMQTT() is called outside the mutex: connect() busy-waits up to 15s
// for CONNACK, and holding the mutex that long starves lwIP packet processing
// on the single-core ESP32-C6. Safe because sendHealthAndRSSI checks connected()
// first and skips publish while a connect is in progress.
void pumpMQTT(uint32_t durationMs) {
  unsigned long end = millis() + durationMs;
  while (millis() < end) {
    if (!mqttClient.connected()) reconnectMQTT();  // outside mutex — may block 15s
    if (xSemaphoreTake(mqttMutex, pdMS_TO_TICKS(100)) == pdTRUE) {
      mqttClient.loop();
      xSemaphoreGive(mqttMutex);
    }
    delay(500);
  }
}

void reconnectMQTT() {
  if (mqttClient.connected()) return;
  static unsigned long lastAttempt = 0;
  if (millis() - lastAttempt < 10000) return;  // one attempt every 10s
  lastAttempt = millis();

  String clientId = "ESP32C6Client-" + String(random(0xffff), HEX);
  Serial.print("[MQTT] Connecting...");
  if (mqttClient.connect(clientId.c_str(), MQTT_USER, MQTT_PASSWORD)) {
    Serial.println(" connected");
  } else {
    Serial.printf(" failed (rc=%d), will retry in 10s\n", mqttClient.state());
  }
}