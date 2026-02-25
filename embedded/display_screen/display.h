#include <GxEPD2_7C.h>
#include <Fonts/FreeMonoBold9pt7b.h>
// --- Pin Definitions ---
#define EPD_BUSY  16 // Purple
#define EPD_RST   1 // White
#define EPD_DC    8 // Green
#define EPD_CS    14 // Orange
#define EPD_SCK   21 // Yellow
#define EPD_MOSI  20  // Blue

#define GxEPD2_DRIVER_CLASS GxEPD2_730c_ACeP_730


void renderWeather(const char* city, const char* firstTimeIso, const char* lastUpdateStr, float* temps, float* precips, float* weathercategories, int count);
void renderBatteryStatus(int x, int y);
void hibernateDisplay();
void renderBwFromBuffer(uint8_t* imageBuffer);
void renderFromBuffer(uint8_t* imageBuffer, int bufferLength);
void extractColorBitmap(uint8_t* inputBuffer, int inputLen, uint8_t targetColor, uint8_t* outputBitmap);
