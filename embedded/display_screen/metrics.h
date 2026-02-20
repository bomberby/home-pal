#ifndef SYSTEM_METRICS_H
#define SYSTEM_METRICS_H
struct SystemMetrics {
  int wifiRssi;
  int batteryPercent;
  float batteryVoltage;
  float temperature;
  float humidity;
  float pressure;
  float altitude;
  float vocIndex;
  float vocRaw;
  float noxIndex;
  float noxRaw;
  bool externalPower;
};

extern SystemMetrics currentMetrics;
#endif