"""
Pure deterministic state resolution — no LLM calls, no image generation.
All methods are static and depend only on external data sources
(weather, calendar, home context, time).
"""
import datetime
import re

from agents.persona.states import (
    STATES, TIME_PERIODS, SITUATION_LABELS, MOOD_MODIFIERS,
    HOLIDAY_PATTERNS,
)
from services.weather_service import get_default_location, get_hourly_forecast, get_current_air_quality, aqi_label
from services.calendar_utils import parse_dt, event_label


class PersonaContext:

    @staticmethod
    def get_time_period() -> str:
        hour = datetime.datetime.now().hour
        if 5 <= hour < 10:
            return "morning"
        if 10 <= hour < 18:
            return "day"
        if 18 <= hour < 21:
            return "evening"
        if 21 <= hour < 24:
            return "night"
        return "late_night"

    @staticmethod
    def get_mood(state_data: dict, period: str | None = None) -> str:
        """Resolve mood: period override → base mood → fallback 'content'."""
        if period:
            override = state_data.get("mood_overrides", {}).get(period)
            if override:
                return override
        return state_data.get("mood", "content")

    @staticmethod
    def get_holiday_override() -> str | None:
        try:
            from services.google_calendar import get_all_events
            events = get_all_events()
            today = datetime.datetime.now().strftime('%Y-%m-%d')
            for event in events:
                event_date = event.get('start', {}).get('date')
                if event_date != today:
                    continue
                summary = event.get('summary', '')
                for pattern, key in HOLIDAY_PATTERNS:
                    if re.search(pattern, summary, re.IGNORECASE):
                        return key
        except Exception:
            pass
        return None

    @staticmethod
    def get_calendar_override() -> tuple[str, str] | None:
        """Return (state_key, event_label) for the nearest active/upcoming meeting, or None."""
        try:
            from services.google_calendar import get_all_events
            events = get_all_events()
            now = datetime.datetime.now(datetime.timezone.utc)
            soon = now + datetime.timedelta(minutes=30)
            for event in events:
                start_str = event.get('start', {}).get('dateTime')
                end_str = event.get('end', {}).get('dateTime')
                if not start_str or not end_str:
                    continue
                start = parse_dt(start_str)
                end = parse_dt(end_str)
                if start <= now < end:
                    return "in_meeting", event_label(event)
                if now <= start <= soon:
                    return "meeting_soon", event_label(event)
        except Exception:
            pass
        return None

    @staticmethod
    def current_weather() -> tuple[float, float, str] | None:
        """Return (temp, precip, sky_description) for the current hour, or None."""
        forecast = get_hourly_forecast(get_default_location(), count=1)
        if not forecast or not forecast['temps']:
            return None
        return (
            forecast['temps'][0],
            forecast['precips'][0],
            forecast['condition_descriptions'][0],
        )

    @staticmethod
    def tomorrow_weather() -> tuple[float, float, str, str] | None:
        """Return (max_temp, min_temp, weather_key, rain_timing) for tomorrow's daytime (6am–9pm), or None.

        rain_timing is a human-readable string like 'morning and afternoon', 'all day', or '' if no rain.
        """
        now  = datetime.datetime.now()
        base = 24 - now.hour
        h6am, hnoon, h6pm, h9pm = base + 6, base + 12, base + 18, base + 21

        forecast = get_hourly_forecast(get_default_location(), count=h9pm + 1)
        if not forecast or len(forecast['temps']) <= h6am:
            return None

        precips   = forecast['precips']
        day_temps = forecast['temps'][h6am:h9pm]
        if not day_temps:
            return None

        max_temp    = max(day_temps)
        min_temp    = min(day_temps)
        max_precip  = max(precips[h6am:h9pm])
        weather_key = PersonaContext.classify_weather(max_temp, max_precip)

        rain_timing = ""
        if max_precip > 0.1:
            periods = [
                name for name, (a, b) in [
                    ("morning",   (h6am,  hnoon)),
                    ("afternoon", (hnoon, h6pm)),
                    ("evening",   (h6pm,  h9pm)),
                ]
                if any(p > 0.1 for p in precips[a:b])
            ]
            rain_timing = "all day" if len(periods) == 3 else " and ".join(periods)

        return max_temp, min_temp, weather_key, rain_timing

    @staticmethod
    def classify_weather(temp: float, precip: float) -> str:
        if temp < 0 and precip > 0.1:
            return "snow"
        if precip > 2.0:
            return "heavy_rain"
        if precip > 0.1:
            return "light_rain"
        if temp < 5:
            return "freezing"
        if temp < 13:
            return "cold"
        if temp < 20:
            return "mild"
        if temp < 27:
            return "warm"
        return "hot"

    @staticmethod
    def lights_on() -> bool:
        try:
            from smart_home.smart_home_service import get_device
            return get_device('led').activated
        except Exception:
            return True  # assume on if unknown

    @staticmethod
    def build_full_context() -> str:
        """Combined context for all generation prompts: time/day/calendar/weather + persona memory."""
        from agents.memory_service import MemoryService
        base = PersonaContext.build_base_context()
        mem = MemoryService.format_for_prompt()
        return f"{base}\n\n{mem}" if mem else base

    @staticmethod
    def _section(title: str, lines: list[str]) -> str:
        return f"{title}:\n" + "\n".join(lines)

    @staticmethod
    def _fmt_events(events: list[tuple]) -> list[str]:
        return [
            f"    - {label} starting at {start.astimezone().strftime('%H:%M')}"
            for start, label in events
        ]

    @staticmethod
    def build_base_context() -> str:
        """Full context for LLM prompts: time, calendar, weather, and home state."""
        now      = datetime.datetime.now()
        period   = PersonaContext.get_time_period()
        day_type = "weekend" if now.weekday() >= 5 else "weekday"

        sections = []

        # Time
        date_part = f"{now.day} {now.strftime('%B')} {now.year}"
        time_part = f"{now.strftime('%H:%M')} (24h)"
        if period == "late_night":
            yesterday  = (now - datetime.timedelta(days=1)).strftime('%A')
            night_type = "work night" if now.weekday() < 5 else "weekend night"
            sections.append(f"The time now: {date_part} — {time_part}, the night between {yesterday} and {now.strftime('%A')}, late {night_type}")
        else:
            sections.append(f"The time now: {now.strftime('%A')}, {date_part} — {time_part}, {period}, {day_type}")

        # Calendar
        cal = PersonaContext.build_calendar_context()
        if cal:
            sections.append(f"###Calendar events and meetings###\n{cal}")

        # Weather outside
        weather_lines = []
        weather = PersonaContext.current_weather()
        if weather:
            temp, precip, sky = weather
            weather_key = PersonaContext.classify_weather(temp, precip)
            label = SITUATION_LABELS.get(weather_key, weather_key.replace('_', ' '))
            sky_str = f", {sky}" if sky else ""
            weather_lines.append(f"  Currently: {label}, {temp:.0f}°C{sky_str}")

        tomorrow = PersonaContext.tomorrow_weather()
        if tomorrow:
            t_max, t_min, t_key, t_rain = tomorrow
            t_label   = SITUATION_LABELS.get(t_key, t_key.replace('_', ' '))
            rain_str  = f" ({t_rain})" if t_rain else ""
            weather_lines.append(f"  Tomorrow: {t_label}{rain_str}, {t_max:.0f}°C high / {t_min:.0f}°C low")

        outdoor_aq = get_current_air_quality(get_default_location())
        if outdoor_aq:
            aq_val, pm25, _ = outdoor_aq
            if aq_val is not None:
                aq_parts = [f"{aqi_label(aq_val)} (AQI {aq_val:.0f})"]
                if pm25 is not None:
                    aq_parts.append(f"PM2.5 {pm25:.1f} µg/m³")
                weather_lines.append(f"  Air quality: {', '.join(aq_parts)}")

        if weather_lines:
            sections.append(PersonaContext._section("Weather outside", weather_lines))

        # Home
        home_lines = []
        try:
            from smart_home.home_context_service import HomeContextService
            indoor_temp = HomeContextService._indoor_temp
            indoor_humidity = HomeContextService._indoor_humidity
            if indoor_temp is not None:
                home_lines.append(f"  Temperature: {indoor_temp:.0f}°C")
            if indoor_humidity is not None:
                home_lines.append(f"  Humidity: {indoor_humidity:.0f}%")
            aq = HomeContextService.air_quality()
            if aq:
                home_lines.append(f"  Air quality: {aq}")
        except Exception:
            pass
        home_lines.append(f"  Lights: {'on' if PersonaContext.lights_on() else 'off'}")
        sections.append(PersonaContext._section("Home", home_lines))

        return "\n\n".join(sections)

    @staticmethod
    def build_calendar_context() -> str | None:
        """Returns calendar lines for today's remaining and tomorrow's events."""
        try:
            from services.google_calendar import get_all_events
            events = get_all_events()
            now_aware = datetime.datetime.now(datetime.timezone.utc)
            now_local = datetime.datetime.now()
            today_str = now_local.strftime('%Y-%m-%d')
            tomorrow_str = (now_local + datetime.timedelta(days=1)).strftime('%Y-%m-%d')

            today_remaining = []
            tomorrow_events = []
            for event in events:
                start_str = event.get('start', {}).get('dateTime')
                if not start_str:
                    continue
                start = parse_dt(start_str)
                start_local_date = start.astimezone().strftime('%Y-%m-%d')
                if start_local_date == today_str and start > now_aware:
                    today_remaining.append((start, event_label(event)))
                elif start_local_date == tomorrow_str:
                    tomorrow_events.append((start, event_label(event)))

            today_remaining.sort(key=lambda x: x[0])
            tomorrow_events.sort(key=lambda x: x[0])

            lines = []
            if today_remaining:
                lines.append("Today remaining events and meeting:")
                lines.extend(PersonaContext._fmt_events(today_remaining))
            else:
                lines.append("Today: no events remaining")
            if tomorrow_events:
                lines.append(f"events and meetings Tomorrow({tomorrow_str}):")
                lines.extend(PersonaContext._fmt_events(tomorrow_events))

            return "\n".join(lines)
        except Exception:
            return None
