#include "esp_camera.h"
#include <WiFi.h>
#include <ESPmDNS.h>
#include <HTTPClient.h>
#include "secrets.h"

// ─── Camera pins — XIAO ESP32-S3 Sense ───────────────────────────────────────
// If using a different board, replace these with the correct pin definitions.
#define PWDN_GPIO_NUM  -1
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM  10
#define SIOD_GPIO_NUM  40
#define SIOC_GPIO_NUM  39
#define Y9_GPIO_NUM    48
#define Y8_GPIO_NUM    11
#define Y7_GPIO_NUM    12
#define Y6_GPIO_NUM    14
#define Y5_GPIO_NUM    16
#define Y4_GPIO_NUM    18
#define Y3_GPIO_NUM    17
#define Y2_GPIO_NUM    15
#define VSYNC_GPIO_NUM 38
#define HREF_GPIO_NUM  47
#define PCLK_GPIO_NUM  13

// ─── Config ───────────────────────────────────────────────────────────────────
const int CAPTURE_INTERVAL_MS = 30000;  // how often to send a snapshot

// ─────────────────────────────────────────────────────────────────────────────

IPAddress serverIP;

bool resolveServer() {
  serverIP = MDNS.queryHost(SERVER_HOST);
  if (serverIP == INADDR_NONE) {
    Serial.printf("[mDNS] Could not resolve %s\n", SERVER_HOST);
    return false;
  }
  Serial.printf("[mDNS] %s → %s\n", SERVER_HOST, serverIP.toString().c_str());
  return true;
}

String snapshotUrl() {
  return String("http://") + serverIP.toString() + ":" + SERVER_PORT
       + "/cam/" + CAM_DEVICE_ID + "/snapshot";
}

void initCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0       = Y2_GPIO_NUM;
  config.pin_d1       = Y3_GPIO_NUM;
  config.pin_d2       = Y4_GPIO_NUM;
  config.pin_d3       = Y5_GPIO_NUM;
  config.pin_d4       = Y6_GPIO_NUM;
  config.pin_d5       = Y7_GPIO_NUM;
  config.pin_d6       = Y8_GPIO_NUM;
  config.pin_d7       = Y9_GPIO_NUM;
  config.pin_xclk     = XCLK_GPIO_NUM;
  config.pin_pclk     = PCLK_GPIO_NUM;
  config.pin_vsync    = VSYNC_GPIO_NUM;
  config.pin_href     = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn     = PWDN_GPIO_NUM;
  config.pin_reset    = RESET_GPIO_NUM;
  config.xclk_freq_hz = 10000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size   = FRAMESIZE_UXGA;  // 1600x1200 — native OV3660 resolution
  config.jpeg_quality = 10;             // higher value = smaller file; quality 4 produced ~4MB JPEGs in dark/noisy scenes, overflowing the ~3.8MB UXGA buffer
  config.fb_count     = 2;
  config.fb_location  = CAMERA_FB_IN_PSRAM;
  config.grab_mode    = CAMERA_GRAB_LATEST;  // discard stale frames during HTTP POST; prevents FB-OVF

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("[Cam] Init failed: 0x%x — halting\n", err);
    while (true) delay(1000);
  }

  // Fine-tune sensor image quality
  sensor_t* s = esp_camera_sensor_get();
  if (s) {
    s->set_brightness(s, 0);    // 0 = let auto-exposure decide; +2 caused overexposure + JPEG grain
    s->set_contrast(s, 1);
    s->set_saturation(s, 1);
    s->set_sharpness(s, 0);     // 0 = no sharpening; higher values amplify noise edges, worsening grain
    s->set_denoise(s, 1);
    s->set_lenc(s, 1);
    s->set_bpc(s, 1);
    s->set_wpc(s, 1);
    s->set_raw_gma(s, 1);
    s->set_awb_gain(s, 1);
    s->set_exposure_ctrl(s, 1);
    s->set_ae_level(s, 0);
    s->set_gain_ctrl(s, 1);
    s->set_agc_gain(s, 10);     // raised cap from 4→10; too-low cap caused underexposure artifacts
  }

  Serial.println("[Cam] Init OK");

  // Warm up: drain frames as fast as possible so auto-exposure settles.
  // No delay between grabs — pausing lets the sensor FIFO back up (FB-OVF).
  for (int i = 0; i < 15; i++) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (fb) esp_camera_fb_return(fb);
  }
  Serial.println("[Cam] Warmup done");
}

void sendSnapshot() {
  if (serverIP == INADDR_NONE) {
    Serial.println("[Cam] No server IP — skipping, will retry resolve");
    resolveServer();
    return;
  }

  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("[Cam] Capture failed");
    return;
  }

  HTTPClient http;
  http.begin(snapshotUrl());
  http.addHeader("Content-Type", "image/jpeg");
  int code = http.POST(fb->buf, fb->len);
  http.end();
  esp_camera_fb_return(fb);

  Serial.printf("[Cam] Sent %d bytes → HTTP %d\n", fb->len, code);
}

void setup() {
  Serial.begin(115200);

  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("[WiFi] Connecting");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\n[WiFi] Connected: %s\n", WiFi.localIP().toString().c_str());

  initCamera();

  if (!MDNS.begin("outdoor-cam")) {
    Serial.println("[mDNS] Failed to start");
  }
  resolveServer();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WiFi] Reconnecting...");
    WiFi.reconnect();
    delay(5000);
    return;
  }

  sendSnapshot();
  delay(CAPTURE_INTERVAL_MS);
}
