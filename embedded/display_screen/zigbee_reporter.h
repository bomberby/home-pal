#include <Arduino.h>
#include "Zigbee.h"

// Zigbee Device Configuration
#define PLAYER_ENDPOINT_ID          1
#define MANUFACTURER_NAME           "OmerBY"
#define MODEL_ID                    "ESP32-C6-Weather-Display"

void sendZigbeeReport();
void buildWifiReport();