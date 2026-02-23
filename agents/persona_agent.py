import json
import re
import datetime
import time
import threading
import requests

from services.weather_service import get_cached_or_fetch, get_default_location
from services.ollama_service import OLLAMA_BASE_URL, OLLAMA_MODEL
from services.calendar_utils import parse_dt
from agents.persona_states import (
    STATES, TIME_PERIODS, CALENDAR_STATES, CONTEXT_STATES,
    HOLIDAY_PATTERNS, HOLIDAY_STATES, SITUATION_LABELS, WMO_LABELS,
    CHARACTER_VOICE, MOOD_MODIFIERS,
)

OLLAMA_URL = f"{OLLAMA_BASE_URL}/api/generate"
QUOTE_TTL = 10 * 60  # seconds


class PersonaAgent:
    _quote_cache: dict[str, tuple[str, float]] = {}  # key -> (text, timestamp)
    _suggestion_generating: set[str] = set()          # guards against duplicate threads

    @staticmethod
    def get_current_state() -> dict:
        from services.home_context_service import HomeContextService
        import config

        # Hub offline: MQTT configured but broker unreachable
        if config.Config.MQTT_BROKER and not HomeContextService.is_connected():
            state_data = CONTEXT_STATES["hub_offline"]
            return PersonaAgent._make_response("hub_offline", state_data, state_data["situation"])

        # Absent: nobody home — skip everything
        if not HomeContextService.is_home():
            return {"state": "absent", "prompt": None, "quote": None, "suggestion": None}

        # Welcome: just arrived home — generate a contextual briefing
        if HomeContextService.is_just_arrived():
            period = PersonaAgent._get_time_period()
            state_data = CONTEXT_STATES["welcome"]
            return PersonaAgent._make_response(
                "welcome", state_data, f"just arrived home in the {period}",
                period=period, custom_quote=PersonaAgent._generate_briefing(),
            )

        # Poor air quality
        if HomeContextService.has_poor_air():
            voc = HomeContextService.get_voc()
            state_data = CONTEXT_STATES["poor_air"]
            return PersonaAgent._make_response(
                "poor_air", state_data,
                f"indoor VOC air quality index is {int(voc)}, air feels stuffy and stale",
            )

        # Indoor temperature / humidity discomfort
        discomfort = HomeContextService.indoor_discomfort()
        if discomfort:
            state_data = CONTEXT_STATES[discomfort]
            temp = HomeContextService._indoor_temp
            humidity = HomeContextService._indoor_humidity
            if discomfort == 'indoor_hot':
                situation = f"the room is {temp:.0f}°C, uncomfortably warm indoors"
            elif discomfort == 'indoor_cold':
                situation = f"the room is only {temp:.0f}°C, uncomfortably cold indoors"
            else:
                situation = f"indoor humidity is {humidity:.0f}%, the air feels damp and sticky"
            return PersonaAgent._make_response(discomfort, state_data, situation)

        # Holiday
        holiday = PersonaAgent._get_holiday_override()
        if holiday:
            state_data = HOLIDAY_STATES[holiday]
            return PersonaAgent._make_response(holiday, state_data, state_data['situation'])

        # Calendar override
        cal_state = PersonaAgent._get_calendar_override()
        if cal_state:
            state_data = CALENDAR_STATES[cal_state]
            situation = "currently in a meeting" if cal_state == "in_meeting" else "a meeting starting in a few minutes"
            return PersonaAgent._make_response(cal_state, state_data, situation)

        # Weather + time of day
        period = PersonaAgent._get_time_period()
        period_data = TIME_PERIODS[period]
        weather = PersonaAgent._current_weather()
        if weather is None:
            base = STATES["mild"]
            return PersonaAgent._make_response(
                "mild", base, f"mild weather in the {period}",
                period=period, fallback=period_data["quote"] or base["quote"],
            )

        temp, precip, wmo_code = weather
        weather_key = PersonaAgent._classify(temp, precip)
        base = STATES[weather_key]
        weather_label = SITUATION_LABELS.get(weather_key, weather_key.replace("_", " "))
        sky = f", {WMO_LABELS[wmo_code]}" if wmo_code in WMO_LABELS else ""
        situation = f"{weather_label} weather ({temp:.0f}°C{sky}) in the {period}"

        return PersonaAgent._make_response(
            weather_key, base, situation,
            period=period, fallback=period_data["quote"] or base["quote"],
        )

    # ------------------------------------------------------------------ #
    #  Response builder                                                    #
    # ------------------------------------------------------------------ #

    @classmethod
    def _make_response(
        cls,
        base_key: str,
        state_data: dict,
        situation: str,
        period: str | None = None,
        custom_quote: str | None = None,
        fallback: str | None = None,
    ) -> dict:
        """Build a complete persona state response dict.

        Handles mood resolution, state key construction, prompt assembly
        (scene + optional period suffix + mood modifier), quote generation,
        and suggestion scheduling in one place.
        """
        mood = cls._get_mood(state_data, period)
        state_key = f"{base_key}_{period}_{mood}" if period else f"{base_key}_{mood}"

        scene = state_data.get("prompt_overrides", {}).get(period, state_data["prompt"]) if period else state_data["prompt"]
        if period:
            scene = scene + ", " + TIME_PERIODS[period]["prompt_suffix"]
        prompt = scene + ", " + MOOD_MODIFIERS[mood]

        effective_fallback = fallback if fallback is not None else state_data.get("quote", "")
        quote = custom_quote if custom_quote is not None else cls._generate_quote(state_key, situation, effective_fallback)
        suggestion = cls._get_suggestion_async(state_key, situation)

        return {"state": state_key, "prompt": prompt, "quote": quote, "suggestion": suggestion}

    # ------------------------------------------------------------------ #
    #  Suggestion — non-blocking, background-generated                    #
    # ------------------------------------------------------------------ #

    @classmethod
    def _get_suggestion_async(cls, state_key: str, situation: str) -> str | None:
        """Return cached suggestion immediately, or None (spawning background generation)."""
        cache_key = f"suggestion_{state_key}"
        cached = cls._quote_cache.get(cache_key)
        if cached and time.time() - cached[1] < QUOTE_TTL:
            return cached[0]
        if state_key not in cls._suggestion_generating:
            cls._suggestion_generating.add(state_key)
            threading.Thread(
                target=cls._generate_suggestion,
                args=(state_key, situation),
                daemon=True,
            ).start()
        return None

    @classmethod
    def _generate_suggestion(cls, state_key: str, situation: str) -> None:
        cache_key = f"suggestion_{state_key}"
        try:
            prompt = (
                CHARACTER_VOICE + " "
                "You just reacted to the current home situation on your dashboard. "
                "Now express one thing you wish you knew that would have made your message more useful or interesting. "
                "Think of specific information you don't currently have access to — such as upcoming holidays, "
                "train schedules, pollen levels, package deliveries, tomorrow's plans, home energy usage. "
                "Express it as a brief, wistful thought in your own voice. Maximum 10 words. "
                f"Current situation: {situation}. Context: {PersonaAgent._build_base_context()}. "
                "Output only the thought, nothing else."
            )
            suggestion = cls._call_ollama(prompt, timeout=10)
            if suggestion:
                cls._quote_cache[cache_key] = (suggestion, time.time())
        except Exception as e:
            print(f"[PersonaAgent] Suggestion generation failed: {e}")
        finally:
            cls._suggestion_generating.discard(state_key)

    # ------------------------------------------------------------------ #
    #  Quote + briefing                                                    #
    # ------------------------------------------------------------------ #

    @classmethod
    def _generate_briefing(cls) -> str:
        cached = cls._quote_cache.get("welcome")
        if cached and time.time() - cached[1] < QUOTE_TTL:
            return cached[0]

        fallback = "Welcome home!"
        prompt = (
            CHARACTER_VOICE + " "
            "Welcome the user who just arrived home with a short warm briefing. Speak directly to them. "
            "Weave in one or two relevant facts from the context naturally. Maximum 2 short sentences. "
            "If using a Japanese greeting, use the time-appropriate one: "
            "'Ohayou' (morning), 'Konnichiwa' (daytime only), 'Konbanwa' (evening or night). "
            "Examples: "
            "'Welcome back! You've got a meeting at 3pm, and it's freezing outside — grab a coat.' / "
            "'Oh, you're home! Nothing on the calendar today, and the weather's actually nice~' / "
            "'Welcome back! Three meetings today — first one at 10am. Cold out there too.' "
            f"Context: {PersonaAgent._build_arrival_context()}. Write the greeting now. Output only the lines, nothing else."
        )
        quote = cls._call_ollama(prompt, timeout=20) or fallback
        cls._quote_cache["welcome"] = (quote, time.time())
        return quote

    @classmethod
    def _generate_quote(cls, state_key: str, situation: str, fallback: str) -> str:
        cached = cls._quote_cache.get(state_key)
        if cached and time.time() - cached[1] < QUOTE_TTL:
            return cached[0]
        prompt = (
            CHARACTER_VOICE + " "
            f"Current situation: {situation}. Context: {PersonaAgent._build_base_context()}. "
            "Be expressive and creative, but stay consistent with the situation — "
            "don't invent weather or events that contradict it. "
            "If you mention a day or month, use the ones provided — never guess. "
            "Never quote the clock time directly. "
            "Write one reaction in that same casual style, maximum 10 words. "
            "Output only the line, nothing else."
        )
        quote = cls._call_ollama(prompt, timeout=10) or fallback
        cls._quote_cache[state_key] = (quote, time.time())
        return quote

    @staticmethod
    def _call_ollama(prompt: str, timeout: int = 10) -> str | None:
        """POST to Ollama and return the stripped response text, or None on failure."""
        try:
            resp = requests.post(
                OLLAMA_URL,
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
                timeout=timeout,
            )
            text = resp.json().get("response", "").strip().strip('"').strip("'")
            return text or None
        except Exception as e:
            print(f"[PersonaAgent] Ollama call failed: {e}")
            return None

    # ------------------------------------------------------------------ #
    #  Context helpers                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_arrival_context() -> str:
        parts = [PersonaAgent._build_base_context()]

        try:
            from services.google_calendar import get_all_events
            events = get_all_events()
            now = datetime.datetime.now(datetime.timezone.utc)
            today_local = datetime.datetime.now().strftime('%Y-%m-%d')
            upcoming = []
            for event in events:
                start_str = event.get('start', {}).get('dateTime')
                if not start_str:
                    continue
                start = parse_dt(start_str)
                if start > now and start.astimezone().strftime('%Y-%m-%d') == today_local:
                    upcoming.append((start, event.get('summary', 'a meeting')))
            upcoming.sort(key=lambda x: x[0])

            if not upcoming:
                parts.append("no meetings left today")
            elif len(upcoming) == 1:
                t = upcoming[0][0].astimezone().strftime('%H:%M')
                parts.append(f"one meeting today: '{upcoming[0][1]}' at {t}")
            else:
                t = upcoming[0][0].astimezone().strftime('%H:%M')
                parts.append(f"{len(upcoming)} meetings today, next is '{upcoming[0][1]}' at {t}")
        except Exception:
            pass

        weather = PersonaAgent._current_weather()
        if weather:
            temp, precip, wmo_code = weather
            weather_key = PersonaAgent._classify(temp, precip)
            label = SITUATION_LABELS.get(weather_key, weather_key.replace('_', ' '))
            sky = f", {WMO_LABELS[wmo_code]}" if wmo_code in WMO_LABELS else ""
            parts.append(f"it's {label} outside, {temp:.0f}°C{sky}")

        return '; '.join(parts) if parts else "nothing specific to report"

    @staticmethod
    def _build_base_context() -> str:
        """Shared time/date context injected into every Ollama call."""
        now = datetime.datetime.now()
        period = PersonaAgent._get_time_period()
        day_type = "weekend" if now.weekday() >= 5 else "weekday"
        if period == "late_night":
            work_night = now.weekday() < 5
            night_type = "work night" if work_night else "weekend night"
            base = f"{now.strftime('%A')}, {now.strftime('%B')}, it's {now.strftime('%H:%M')}, late {night_type}"
        else:
            base = f"{now.strftime('%A')}, {now.strftime('%B')}, it's {now.strftime('%H:%M')} ({period}, {day_type})"
        cal = PersonaAgent._build_calendar_context()
        return f"{base}; {cal}" if cal else base

    @staticmethod
    def _build_calendar_context() -> str | None:
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
                    today_remaining.append((start, event.get('summary', 'a meeting')))
                elif start_local_date == tomorrow_str:
                    tomorrow_events.append((start, event.get('summary', 'a meeting')))

            today_remaining.sort(key=lambda x: x[0])
            tomorrow_events.sort(key=lambda x: x[0])

            parts = []
            if today_remaining:
                t = today_remaining[0][0].astimezone().strftime('%H:%M')
                count = len(today_remaining)
                label = f"'{today_remaining[0][1]}' at {t}" if count == 1 else f"{count} events left today, next '{today_remaining[0][1]}' at {t}"
                parts.append(label)
            if tomorrow_events:
                t = tomorrow_events[0][0].astimezone().strftime('%H:%M')
                count = len(tomorrow_events)
                label = f"tomorrow: '{tomorrow_events[0][1]}' at {t}" if count == 1 else f"tomorrow: {count} events, first '{tomorrow_events[0][1]}' at {t}"
                parts.append(label)

            return '; '.join(parts) if parts else None
        except Exception:
            return None

    @staticmethod
    def _get_time_period() -> str:
        hour = datetime.datetime.now().hour
        if 5 <= hour < 10:
            return "morning"
        if 10 <= hour < 18:
            return "day"
        if 18 <= hour < 22:
            return "evening"
        if 22 <= hour < 24:
            return "night"
        return "late_night"

    @staticmethod
    def _get_holiday_override() -> str | None:
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
    def _get_calendar_override() -> str | None:
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
                    return "in_meeting"
                if now <= start <= soon:
                    return "meeting_soon"
        except Exception:
            pass
        return None

    @staticmethod
    def _current_weather() -> tuple[float, float, int | None] | None:
        """Return (temp, precip, wmo_code) for the current hour, or None if unavailable."""
        try:
            location = get_default_location()
            city_data = get_cached_or_fetch([location]).get(location)
            if not city_data:
                return None
            temperatures = json.loads(city_data["hourly_temperatures"])
            precipitation = json.loads(city_data["hourly_precipitation"])
            weathercodes = json.loads(city_data["hourly_weathercodes"]) if city_data.get("hourly_weathercodes") else []
            first_time = datetime.datetime.fromisoformat(city_data["first_time"])
            idx = int((datetime.datetime.now() - first_time).total_seconds() // 3600)
            idx = max(0, min(idx, len(temperatures) - 1))
            return (
                temperatures[idx],
                precipitation[idx],
                weathercodes[idx] if idx < len(weathercodes) else None,
            )
        except Exception:
            return None

    @staticmethod
    def _get_mood(state_data: dict, period: str | None = None) -> str:
        """Resolve mood: period override → base mood → fallback 'content'."""
        if period:
            override = state_data.get("mood_overrides", {}).get(period)
            if override:
                return override
        return state_data.get("mood", "content")

    @staticmethod
    def _classify(temp: float, precip: float) -> str:
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
