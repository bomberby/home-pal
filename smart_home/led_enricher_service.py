from typing import List, Dict
from services.weather_service import get_cached_or_fetch, get_default_location
from services.calendar_utils import is_event_on, parse_dt
import json
import datetime
import services.google_calendar as google_calendar
import re
from cache import cache


class LedEnricherService:
    NUM_LEDS = 60

    def __init__(self, device):
        self.device = device
        self.indicators: List[Dict] = []

    def add_indicator(self, indicator_payload: Dict):
        """Add an indicator (calendar, weather, alert...)."""
        self.indicators.append(indicator_payload)

    def add_all_indicators(self):
        alert_indicator = {
            "type": "alert",
            "priority": 2,
            "leds": [{"index": 57, "color": [255, 0, 0], "brightness": 255}, {"index": 59, "color": [255, 0, 0], "brightness": 255}],
            "animations": [{"type": "flash", "color": [255, 0, 0], "brightness": 255, "interval_ms": 4000}]
        }

        weather = self.weather_indicator()
        self.add_indicator(weather)

        try:
            events = google_calendar.get_all_events()
            now = datetime.datetime.now()
            now_tz = datetime.datetime.now(datetime.timezone.utc).astimezone()
            today_str = now.strftime('%Y-%m-%d')
            tomorrow_str = (now + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
            today = [e for e in events if is_event_on(e, today_str)]
            tomorrow = [e for e in events if is_event_on(e, tomorrow_str)]

            soonest = None
            for event in today:
                start_str = event.get('start', {}).get('dateTime')
                if not start_str:
                    continue
                time_until = (parse_dt(start_str) - now_tz).total_seconds()
                if 0 <= time_until <= 3600:
                    if soonest is None or time_until < soonest:
                        soonest = time_until
            if soonest is not None:
                weather["animations"] = [{
                    "type": "comet",
                    "color": [255, 240, 200],
                    "brightness": 70,
                    "tail_length": 5,
                    "interval_ms": max(30, int(soonest / 6))
                }]

            self.add_indicator(self.special_occasions(today, tomorrow))
            self.add_indicator(self.calendar_indicator(today))
        except Exception as e:
            print("Calendar exception:", e)
            self.add_indicator(alert_indicator)

    def weather_indicator(self):
        location = get_default_location()
        weather_data = get_cached_or_fetch([location])
        city_data = weather_data.get(location)

        if not city_data:
            raise Exception(f"Weather data for {location} is unavailable.")

        precipitation = json.loads(city_data["hourly_precipitation"])
        temperatures = json.loads(city_data["hourly_temperatures"])
        first_time = datetime.datetime.fromisoformat(city_data["first_time"])

        now = datetime.datetime.now()
        hours_since_start = int((now - first_time).total_seconds() // 3600)
        look_ahead = 36
        end_index = hours_since_start + look_ahead

        today_precip = precipitation[hours_since_start:end_index]
        today_temps = temperatures[hours_since_start:end_index]

        return LedEnricherService.build_weather_indicator(today_temps, today_precip)

    @staticmethod
    def build_weather_indicator(today_temps, today_precipitation, base_led_index=5):
        leds = []

        for i, (temp, precip) in enumerate(zip(today_temps, today_precipitation)):
            led = {
                "index": base_led_index + i,
                "color": LedEnricherService.temp_to_color(temp),
                "brightness": 190
            }

            pulse_anim = LedEnricherService.precip_to_pulse_animation(precip)
            if pulse_anim:
                led["animations"] = [pulse_anim]

            leds.append(led)

        return {
            "type": "weather",
            "priority": 0,
            "leds": leds
        }

    @staticmethod
    def temp_to_color(temp):
        temp = max(-5, min(35, temp))

        if temp <= 0:
            return [0, 0, 255]
        elif temp <= 15:
            t = (temp - 0) / 15
            return [0, int(255 * t), 255 - int(255 * t)]
        elif temp <= 30:
            t = (temp - 15) / 15
            return [int(255 * t), 255 - int(255 * t), 0]
        else:
            return [255, 0, 0]

    @staticmethod
    def precip_to_pulse_animation(precip_mm):
        """Returns a pulse animation dict or None if no precipitation."""
        if precip_mm <= 0:
            return None

        brightness = min(155, int(precip_mm * 60))
        interval = max(150, int(600 - precip_mm * 300))

        return {
            "type": "pulse",
            "color": [180, 0, 100],
            "brightness": brightness,
            "interval_ms": interval
        }

    def special_occasions(self, today_events, tomorrow_events):
        leds = []

        # Christmas
        pattern = r"Christmas"
        event = [event for event in today_events if re.search(pattern, event.get('summary', ''))]
        if event:
            for led in range(10):
                leds.append({"index": led, "color": [10, 250, 5], "brightness": 100, "animations": [{"type": "pulse",
                    "color": [180 + led * 2, 20, 40 - led * 2],
                    "brightness": 150 + led * 2,
                    "interval_ms": 400 - led * 2
                }]})

        # Hanuka
        pattern = r"Hanukkah \(Day (\d)\)"
        event = [event for event in tomorrow_events if re.search(pattern, event.get('summary', ''))]
        if event:
            match = re.search(pattern, event[0]['summary'])
            days_of_hanuka = int(match.group(1))
            for day in range(days_of_hanuka + 1):
                leds.append({"index": day, "color": [255, 100, 0], "brightness": 100, "animations": [{"type": "pulse",
                    "color": [180 + day * 2, 20, 40 - day * 2],
                    "brightness": 150 + day * 2,
                    "interval_ms": 20 - day * 2
                }]})

        return {
            "type": "occasions",
            "priority": 1,
            "leds": leds,
            "animations": []
        }

    def calendar_indicator(self, today_events):
        leds = []
        max_brightness = 250
        now = datetime.datetime.now(datetime.timezone.utc).astimezone()

        timed_events = [event for event in today_events if event.get('start', {}).get('dateTime')]
        if timed_events:
            current_and_future_events = []
            for event in timed_events:
                event_time = parse_dt(event.get('end', {}).get('dateTime'))
                if event_time > now:
                    current_and_future_events.append(event)

            if current_and_future_events:
                i = 0
                for event in current_and_future_events[:5]:
                    color = event.get('calendar_color_rgb', [200, 200, 5])

                    time_until = (parse_dt(event.get('start', {}).get('dateTime')) - now).total_seconds()
                    if time_until < 0 or time_until > 3600:
                        brightness = int(min(max(8, max_brightness - time_until / 60), max_brightness))
                        led_animations = []
                    else:
                        brightness = max_brightness
                        led_animations = [{"type": "flash", "color": color, "brightness": 255, "interval_ms": int(time_until)}]

                    leds.append({"index": i, "color": color, "brightness": brightness, "animations": led_animations})
                    i += 1

        return {
            "type": "calendar",
            "priority": 3,
            "leds": leds
        }

    def today_events(self):
        today = datetime.datetime.now()
        today_str = today.strftime('%Y-%m-%d')
        events = google_calendar.get_all_events()
        return [event for event in events if is_event_on(event, today_str)]

    def tomorrow_events(self):
        tomorrow = datetime.datetime.now() + datetime.timedelta(days=1)
        tomorrow_str = tomorrow.strftime('%Y-%m-%d')
        events = google_calendar.get_all_events()
        return [event for event in events if is_event_on(event, tomorrow_str)]

    def get_led_state(self) -> Dict:
        """
        Returns JSON-serializable payload for ESP32.
        Resolves overlapping LEDs using priority if provided.
        """
        if not self.device.activated:
            return {"activated": False, "id": self.device.id, "mode": "indicator_mode", "indicators": []}

        if self.device.mode == "rainbow":
            return {"activated": True, "id": self.device.id, "mode": "rainbow"}

        self.add_all_indicators()

        return {
            "activated": True,
            "id": self.device.id,
            "mode": "indicator_mode",
            "indicators": self.indicators
        }
