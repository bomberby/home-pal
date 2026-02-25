#include "display.h"
#include "metrics.h" // Needed for rendering metrics on screen

GxEPD2_DRIVER_CLASS* inner_driver = nullptr; 
GxEPD2_7C < GxEPD2_DRIVER_CLASS, GxEPD2_DRIVER_CLASS::HEIGHT / 4 > *display = nullptr;
void initDisplay(){
  if (display != nullptr) {
    return;
  }
  pinMode(15, OUTPUT); // Needed to mute noise in serial due to driver trying to use it regardless of pinout
  SPI.begin(EPD_SCK, -1, EPD_MOSI, EPD_CS); 
  inner_driver = new GxEPD2_DRIVER_CLASS(EPD_CS, EPD_DC, EPD_RST, EPD_BUSY);
  display = new GxEPD2_7C < GxEPD2_DRIVER_CLASS, GxEPD2_DRIVER_CLASS::HEIGHT / 4 > (*inner_driver);
  display->init(115200, true, 50, false);
  display->setRotation(2); // screen is rotated
}

// --- Weather Icon Helpers ---

// Draws a white cloud shape centred at (cx, cy). Shared by overcast/rain/drizzle/storm icons.
static void drawCloud(int cx, int cy) {
  display->fillCircle(cx - 4, cy,     5, GxEPD_WHITE);
  display->fillCircle(cx + 3, cy - 2, 6, GxEPD_WHITE);
  display->fillRoundRect(cx - 9, cy + 2, 19, 8, 3, GxEPD_WHITE);
  display->drawCircle(cx - 4, cy,     5, GxEPD_BLACK);
  display->drawCircle(cx + 3, cy - 2, 6, GxEPD_BLACK);
  display->drawRoundRect(cx - 9, cy + 2, 19, 8, 3, GxEPD_BLACK);
  // White patch to erase the circle-arc segments that overlap inside the cloud body
  display->fillRect(cx - 8, cy + 2, 17, 4, GxEPD_WHITE);
}

// Draws a ~22×22 px weather icon centred at (cx, cy) for a given severity category (0–7).
static void renderWeatherIcon(int cx, int cy, int category) {
  switch (category) {

    case 0: { // Clear — yellow sun with orange rays
      display->fillCircle(cx, cy, 7, GxEPD_YELLOW);
      for (int a = 0; a < 8; a++) {
        float rad = a * PI / 4.0f;
        display->drawLine(cx + (int)(cosf(rad) * 10), cy + (int)(sinf(rad) * 10),
                          cx + (int)(cosf(rad) * 13), cy + (int)(sinf(rad) * 13), GxEPD_ORANGE);
      }
      break;
    }

    case 1: { // Partly cloudy — sun upper-left, white cloud lower-right (covers some rays)
      display->fillCircle(cx - 4, cy - 3, 6, GxEPD_YELLOW);
      for (int a = 0; a < 8; a++) {
        float rad = a * PI / 4.0f;
        display->drawLine(cx - 4 + (int)(cosf(rad) * 8),  cy - 3 + (int)(sinf(rad) * 8),
                          cx - 4 + (int)(cosf(rad) * 11), cy - 3 + (int)(sinf(rad) * 11), GxEPD_ORANGE);
      }
      // Cloud overlapping lower-right; white fill erases the sun rays behind it naturally
      display->fillCircle(cx + 2, cy + 2, 5, GxEPD_WHITE);
      display->fillCircle(cx + 8, cy + 4, 4, GxEPD_WHITE);
      display->fillRoundRect(cx - 1, cy + 4, 16, 7, 3, GxEPD_WHITE);
      display->drawCircle(cx + 2, cy + 2, 5, GxEPD_BLACK);
      display->drawCircle(cx + 8, cy + 4, 4, GxEPD_BLACK);
      display->drawRoundRect(cx - 1, cy + 4, 16, 7, 3, GxEPD_BLACK);
      display->fillRect(cx, cy + 4, 14, 3, GxEPD_WHITE); // erase circle overlap inside cloud
      break;
    }

    case 2: { // Overcast — plain cloud
      drawCloud(cx, cy);
      break;
    }

    case 3: { // Fog — three horizontal lines of decreasing width
      display->drawLine(cx - 9,  cy - 5, cx + 9,  cy - 5, GxEPD_BLACK);
      display->drawLine(cx - 11, cy,     cx + 11, cy,     GxEPD_BLACK);
      display->drawLine(cx - 9,  cy + 5, cx + 9,  cy + 5, GxEPD_BLACK);
      break;
    }

    case 4: { // Drizzle — cloud + 3 blue dots
      drawCloud(cx, cy - 4);
      display->fillCircle(cx - 5, cy + 8, 2, GxEPD_BLUE);
      display->fillCircle(cx,     cy + 9, 2, GxEPD_BLUE);
      display->fillCircle(cx + 5, cy + 8, 2, GxEPD_BLUE);
      break;
    }

    case 5: { // Rain — cloud + 3 diagonal blue lines (drawn double for 2px weight)
      drawCloud(cx, cy - 5);
      for (int r = -1; r <= 1; r++) {
        int rx = cx + r * 5;
        display->drawLine(rx,     cy + 2, rx - 3, cy + 9, GxEPD_BLUE);
        display->drawLine(rx + 1, cy + 2, rx - 2, cy + 9, GxEPD_BLUE);
      }
      break;
    }

    case 6: { // Snow — 6-armed asterisk with blue tip dots
      for (int a = 0; a < 3; a++) {
        float rad = a * PI / 3.0f;
        display->drawLine(cx + (int)(cosf(rad) * 9), cy + (int)(sinf(rad) * 9),
                          cx - (int)(cosf(rad) * 9), cy - (int)(sinf(rad) * 9), GxEPD_BLACK);
      }
      for (int a = 0; a < 6; a++) {
        float rad = a * PI / 3.0f;
        display->fillCircle(cx + (int)(cosf(rad) * 9), cy + (int)(sinf(rad) * 9), 2, GxEPD_BLUE);
      }
      break;
    }

    case 7: { // Thunderstorm — cloud + orange lightning bolt
      drawCloud(cx, cy - 5);
      display->fillTriangle(cx + 2, cy + 1,  cx - 3, cy + 7,  cx + 1, cy + 7,  GxEPD_ORANGE);
      display->fillTriangle(cx - 1, cy + 7,  cx - 5, cy + 13, cx + 3, cy + 7,  GxEPD_ORANGE);
      break;
    }
  }
}

void renderWeather(const char* city, const char* firstTimeIso, const char* lastUpdateStr, float* temps, float* precips, float* weathercategories, int count){
  initDisplay();

  // 1. Time Setup
  struct tm start_tm = {0};
  sscanf(firstTimeIso, "%d-%d-%dT%d:%d", &start_tm.tm_year, &start_tm.tm_mon, &start_tm.tm_mday, &start_tm.tm_hour, &start_tm.tm_min);
  start_tm.tm_year -= 1900; start_tm.tm_mon -= 1;
  time_t startTimeRaw = mktime(&start_tm);
  
  time_t now;
  time(&now); 

  // 2. Data Windowing (4 Days)
  int displayHours = min(count, 96);
  float currentTemp = 0;

  // 3. Find Extremes for Scaling
  float minT = 100, maxT = -100, maxP = 1.0; 
  for (int i = 0; i < displayHours; i++) {
    if (temps[i] < minT) minT = temps[i];
    if (temps[i] > maxT) maxT = temps[i];
    if (precips[i] > maxP) maxP = precips[i];
    
    // Capture "Now" temperature for the header
    time_t currentTime = startTimeRaw + (i * 3600);
    if (abs(currentTime - now) < 1800) currentTemp = temps[i];
  }
  
  int yAxisMin = floor(minT / 5.0) * 5 - 5;
  int yAxisMax = ceil(maxT / 5.0) * 5 + 5;

  display->firstPage();
  do {
    display->fillScreen(GxEPD_WHITE);
    
    // Layout Constants
    int gX = 80;        // Left Gutter
    int gY = 400;       // Bottom
    int gW = 620;       // Width
    int gH = 300;       // Height
    float stepX = (float)gW / (displayHours - 1);

    // --- RENDER SYSTEM UI ---
    // We pass the top-right coordinates
    renderBatteryStatus(720, 20);

    // --- 4. Draw LEFT Y-AXIS (Temperature) ---
    display->setFont(nullptr); // Small font
    display->setTextColor(GxEPD_BLACK);
    for (int t = yAxisMin; t <= yAxisMax; t += 5) {
      int yPos = gY - map(t, yAxisMin, yAxisMax, 0, gH);
      display->drawLine(gX, yPos, gX + gW, yPos, GxEPD_BLACK); // Grid line
      display->setCursor(gX - 50, yPos - 4);
      display->printf("%d C", t);
    }

    // --- 5. Draw RIGHT Y-AXIS (Precipitation) ---
    display->setTextColor(GxEPD_BLUE);
    for (float p = 0; p <= maxP; p += 1.0) {
      int yPos = gY - map((int)(p * 10), 0, (int)(maxP * 10), 0, gH);
      display->setCursor(gX + gW + 10, yPos - 4);
      display->printf("%.0fmm", p);
    }

    // --- 6. Draw X-AXIS & DATA ---
    for (int i = 0; i < displayHours; i++) {
      int xPos = gX + (i * stepX);
      time_t currentTime = startTimeRaw + (i * 3600);
      struct tm* timeinfo = localtime(&currentTime);

      // Date Labels at Midnight
      if (timeinfo->tm_hour == 0 || i == 0) {
        display->drawLine(xPos, gY, xPos, gY - gH, GxEPD_BLACK);
        display->setTextColor(GxEPD_BLACK);
        char dateBuf[12];
        strftime(dateBuf, sizeof(dateBuf), "%a %d", timeinfo);
        display->setCursor(xPos + 5, gY + 12);
        display->print(dateBuf);

        // Pick the worst severity category in the next 24 h and draw its icon above the chart
        int worstCat = (int)weathercategories[i];
        for (int j = i + 1; j < min(i + 24, displayHours); j++) {
          int cat = (int)weathercategories[j];
          if (cat > worstCat) worstCat = cat;
        }
        renderWeatherIcon(xPos + 10, gY - gH - 25, worstCat);
      }

      // "NOW" Marker
      if (abs(currentTime - now) < 1800) {
        for(int dotY = gY; dotY > gY-gH; dotY-=8) display->drawLine(xPos, dotY, xPos, dotY-4, GxEPD_BLACK);
        display->setCursor(xPos - 10, gY - gH - 12);
        display->print("NOW");
      }

      // Rain Bars
      if (precips[i] > 0.0) {
        int barH = map((int)(precips[i] * 10), 0, (int)(maxP * 10), 0, gH);
        display->fillRect(xPos, gY - barH, max(3, (int)stepX - 1), barH, GxEPD_BLUE);
      }

      // Temperature Line
      if (i < displayHours - 1) {
        int ty1 = gY - map((int)(temps[i] * 10), yAxisMin * 10, yAxisMax * 10, 0, gH);
        int ty2 = gY - map((int)(temps[i+1] * 10), yAxisMin * 10, yAxisMax * 10, 0, gH);
        display->drawLine(xPos, ty1, xPos + stepX, ty2, GxEPD_RED);
        display->drawLine(xPos, ty1+1, xPos + stepX, ty2+1, GxEPD_RED); // Bold
      }
    }

    // --- 7. Header & Legend ---
    display->drawRect(gX, gY - gH, gW, gH, GxEPD_BLACK);
    display->setFont(&FreeMonoBold9pt7b);
    display->setTextColor(GxEPD_BLACK);
    display->setCursor(gX, 40);
    display->printf("%s Outlook", city);
    
    // Bold Current Temp in top right
    display->setCursor(gX + gW - 180, 40);
    display->printf("NOW: %.1f C", currentTemp);

  } while (display->nextPage());
}



void renderBatteryStatus(int x, int y) {
  display->setTextColor(GxEPD_BLACK);
  display->setFont(nullptr); // Use system font for small UI elements

  // 1. Check for USB / External Power
  bool isUSB = (currentMetrics.batteryPercent > 100);

  // 2. Draw Battery Frame
  display->drawRect(x, y, 40, 20, GxEPD_BLACK);     // Main body
  display->fillRect(x + 40, y + 5, 3, 10, GxEPD_BLACK); // Positive terminal tip

  if (isUSB) {
    // Render "USB" or a Bolt icon inside the battery frame
    display->setCursor(x + 10, y + 6);
    display->print("USB");
  } else {
    // 3. Draw Battery Fill
    int fillWidth = (36 * currentMetrics.batteryPercent) / 100;
    
    // Color logic: Red if critical, otherwise Black/Green
    uint16_t color = (currentMetrics.batteryPercent < 20) ? GxEPD_RED : GxEPD_BLACK;
    display->fillRect(x + 2, y + 2, fillWidth, 16, color);
    
    // 4. Render Percentage Text
    display->setCursor(x - 40, y + 6);
    display->printf("%d%%", currentMetrics.batteryPercent);
  }
}

void renderBwFromBuffer(uint8_t* imageBuffer) {
  uint8_t* bmpContent = imageBuffer + 62; // Offset by 54 for BMP header +8 for... IDK
  initDisplay();
  display->setRotation(0); // BMP files are actually reversed, but screen is rotated
  display->setFullWindow();
  display->firstPage();
  do {
    display->fillScreen(GxEPD_WHITE); 
    display->drawBitmap(0, 0, bmpContent, 800, 480, GxEPD_BLACK);
  } while (display->nextPage());
}

void renderFromBuffer(uint8_t* imageBuffer, int bufferLength) {
  initDisplay();
  display->setRotation(2); // screen is rotated 180
  display->setFullWindow();

  // Explicit mapping based on your Python palette
  // 0:black, 1:white, 2:green, 3:blue, 4:red, 5:yellow, 6:orange
  const uint16_t paletteMap[] = {
    GxEPD_BLACK,  // 0
    GxEPD_WHITE,  // 1
    GxEPD_GREEN,  // 2
    GxEPD_BLUE,   // 3
    GxEPD_RED,    // 4
    GxEPD_YELLOW, // 5
    GxEPD_ORANGE  // 6
  };

  int16_t w = 800; // Hardcoded for your specific display width

  display->firstPage();
  do {
    display->fillScreen(GxEPD_BLUE);

    for (int i = 0; i < bufferLength; i++) {
      uint8_t input = imageBuffer[i];
      
      // Extracting nibbles: 
      // Most 4-bit streams pack High nibble first, then Low nibble.
      uint8_t p1 = (input >> 4) & 0x0F; 
      uint8_t p2 = input & 0x0F;

      // Pixel 1
      if (p1 <= 6) {
        int pixelIdx = i * 2;
        display->drawPixel(pixelIdx % w, pixelIdx / w, paletteMap[p1]);
      }

      // Pixel 2
      if (p2 <= 6) {
        int pixelIdx = (i * 2) + 1;
        display->drawPixel(pixelIdx % w, pixelIdx / w, paletteMap[p2]);
      }
    }
  } while (display->nextPage());
}


void hibernateDisplay() {
  if (display) {
    display->hibernate(); // Puts the display hardware into lowest power mode
  }
}
