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
from services.weather_service import get_default_location, get_hourly_forecast
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
    def tomorrow_weather() -> tuple[float, float, str] | None:
        """Return (max_temp, max_hourly_precip, weather_key) for tomorrow's daytime (6am–9pm), or None."""
        now = datetime.datetime.now()
        hours_to_6am = 24 - now.hour + 6
        hours_to_9pm = 24 - now.hour + 21
        forecast = get_hourly_forecast(get_default_location(), count=hours_to_9pm + 1)
        if not forecast or len(forecast['temps']) <= hours_to_6am:
            return None
        day_temps = forecast['temps'][hours_to_6am:hours_to_9pm]
        day_precips = forecast['precips'][hours_to_6am:hours_to_9pm]
        if not day_temps:
            return None
        max_temp = max(day_temps)
        max_precip = max(day_precips)
        return max_temp, max_precip, PersonaContext.classify_weather(max_temp, max_precip)

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
        return f"{base}. {mem}" if mem else base

    @staticmethod
    def build_base_context() -> str:
        """Full context for LLM prompts: time, calendar, and current weather."""
        now = datetime.datetime.now()
        period = PersonaContext.get_time_period()
        day_type = "weekend" if now.weekday() >= 5 else "weekday"
        if period == "late_night":
            work_night = now.weekday() < 5
            night_type = "work night" if work_night else "weekend night"
            parts = [f"{now.strftime('%A')}, {now.strftime('%B')} {now.day} {now.year}, it's {now.strftime('%H:%M')} (24h clock), late {night_type}"]
        else:
            parts = [f"{now.strftime('%A')}, {now.strftime('%B')} {now.day} {now.year}, it's {now.strftime('%H:%M')} (24h clock, {period}, {day_type})"]

        cal = PersonaContext.build_calendar_context()
        if cal:
            parts.append(cal)

        weather = PersonaContext.current_weather()
        if weather:
            temp, precip, sky = weather
            weather_key = PersonaContext.classify_weather(temp, precip)
            label = SITUATION_LABELS.get(weather_key, weather_key.replace('_', ' '))
            sky_str = f", {sky}" if sky else ""
            parts.append(f"outside: {label}, {temp:.0f}°C{sky_str}")

        tomorrow = PersonaContext.tomorrow_weather()
        if tomorrow:
            t_temp, t_precip, t_key = tomorrow
            t_label = SITUATION_LABELS.get(t_key, t_key.replace('_', ' '))
            parts.append(f"tomorrow: {t_label}, {t_temp:.0f}°C")

        try:
            from smart_home.home_context_service import HomeContextService
            indoor_parts = []
            t = HomeContextService._indoor_temp
            h = HomeContextService._indoor_humidity
            if t is not None:
                indoor_parts.append(f"{t:.0f}°C")
            if h is not None:
                indoor_parts.append(f"{h:.0f}% humidity")
            aq = HomeContextService.air_quality()
            if aq and aq != 'good':
                indoor_parts.append(f"air quality {aq}")
            if indoor_parts:
                parts.append("indoors: " + ", ".join(indoor_parts))
        except Exception:
            pass

        parts.append("lights are on" if PersonaContext.lights_on() else "lights are off")
        return "; ".join(parts)

    @staticmethod
    def build_calendar_context() -> str | None:
        """Returns a short calendar summary for today's remaining and tomorrow's events."""
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

            parts = []
            if today_remaining:
                t = today_remaining[0][0].astimezone().strftime('%H:%M')
                count = len(today_remaining)
                label = (
                    f"<event>{today_remaining[0][1]}</event> at {t}" if count == 1
                    else f"{count} events left today, next <event>{today_remaining[0][1]}</event> at {t}"
                )
                parts.append(label)
            else:
                parts.append("no meetings left today")

            if tomorrow_events:
                t = tomorrow_events[0][0].astimezone().strftime('%H:%M')
                count = len(tomorrow_events)
                label = (
                    f"tomorrow: <event>{tomorrow_events[0][1]}</event> at {t}" if count == 1
                    else f"tomorrow: {count} events, first <event>{tomorrow_events[0][1]}</event> at {t}"
                )
                parts.append(label)

            return '; '.join(parts)
        except Exception:
            return None
