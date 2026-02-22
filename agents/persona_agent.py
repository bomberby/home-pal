import json
import re
import datetime
import time
import requests

from services.weather_service import get_cached_or_fetch, get_default_location
from services.ollama_service import OLLAMA_BASE_URL, OLLAMA_MODEL
from agents.persona_states import STATES, TIME_PERIODS, CALENDAR_STATES, CONTEXT_STATES, HOLIDAY_PATTERNS, HOLIDAY_STATES, SITUATION_LABELS

OLLAMA_URL = f"{OLLAMA_BASE_URL}/api/generate"
QUOTE_TTL = 10 * 60  # seconds — regenerate on each persona refresh cycle


class PersonaAgent:
    _quote_cache: dict[str, tuple[str, float]] = {}  # state_key -> (quote, timestamp)

    @staticmethod
    def get_current_state() -> dict:
        from services.home_context_service import HomeContextService
        import config

        # Hub offline: MQTT configured but broker unreachable
        if config.Config.MQTT_BROKER and not HomeContextService.is_connected():
            state_data = CONTEXT_STATES["hub_offline"]
            quote = PersonaAgent._generate_quote("hub_offline", state_data["situation"], state_data["quote"])
            return {"state": "hub_offline", "prompt": state_data["prompt"], "quote": quote}

        # Absent: nobody home — skip everything
        if not HomeContextService.is_home():
            return {"state": "absent", "prompt": None, "quote": None}

        # Welcome: just arrived home — generate a contextual briefing
        if HomeContextService.is_just_arrived():
            period = PersonaAgent._get_time_period()
            period_data = TIME_PERIODS[period]
            state_data = CONTEXT_STATES["welcome"]
            prompt = state_data["prompt"] + ", " + period_data["prompt_suffix"]
            quote = PersonaAgent._generate_briefing()
            return {"state": f"welcome_{period}", "prompt": prompt, "quote": quote}

        # Poor air quality
        if HomeContextService.has_poor_air():
            voc = HomeContextService.get_voc()
            state_data = CONTEXT_STATES["poor_air"]
            situation = f"indoor VOC air quality index is {int(voc)}, air feels stuffy and stale"
            quote = PersonaAgent._generate_quote("poor_air", situation, state_data["quote"])
            return {"state": "poor_air", "prompt": state_data["prompt"], "quote": quote}

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
            quote = PersonaAgent._generate_quote(discomfort, situation, state_data["quote"])
            return {"state": discomfort, "prompt": state_data["prompt"], "quote": quote}

        # Holiday
        holiday = PersonaAgent._get_holiday_override()
        if holiday:
            state_data = HOLIDAY_STATES[holiday]
            quote = PersonaAgent._generate_quote(holiday, state_data['situation'], state_data['quote'])
            return {"state": holiday, "prompt": state_data["prompt"], "quote": quote}

        # Calendar override
        cal_state = PersonaAgent._get_calendar_override()
        if cal_state:
            state_data = CALENDAR_STATES[cal_state]
            situation = "currently in a meeting" if cal_state == "in_meeting" else "a meeting starting in a few minutes"
            quote = PersonaAgent._generate_quote(cal_state, situation, state_data["quote"])
            return {"state": cal_state, "prompt": state_data["prompt"], "quote": quote}

        # Weather + time of day
        location = get_default_location()
        weather_data = get_cached_or_fetch([location])
        city_data = weather_data.get(location)

        period = PersonaAgent._get_time_period()
        period_data = TIME_PERIODS[period]

        if not city_data:
            base = STATES["mild"]
            state_key = f"mild_{period}"
            situation = f"mild weather in the {period}"
            fallback = period_data["quote"] or base["quote"]
            return {
                "state": state_key,
                "prompt": base["prompt"] + ", " + period_data["prompt_suffix"],
                "quote": PersonaAgent._generate_quote(state_key, situation, fallback),
            }

        precipitation = json.loads(city_data["hourly_precipitation"])
        temperatures = json.loads(city_data["hourly_temperatures"])
        first_time = datetime.datetime.fromisoformat(city_data["first_time"])

        now = datetime.datetime.now()
        current_index = int((now - first_time).total_seconds() // 3600)
        current_index = max(0, min(current_index, len(temperatures) - 1))

        temp = temperatures[current_index]
        precip = precipitation[current_index]

        weather_key = PersonaAgent._classify(temp, precip)
        state_key = f"{weather_key}_{period}"
        base = STATES[weather_key]
        prompt = base["prompt"] + ", " + period_data["prompt_suffix"]

        weather_label = SITUATION_LABELS.get(weather_key, weather_key.replace("_", " "))
        situation = f"{weather_label} weather ({temp:.0f}°C) in the {period}"
        fallback = period_data["quote"] or base["quote"]
        quote = PersonaAgent._generate_quote(state_key, situation, fallback)

        return {"state": state_key, "prompt": prompt, "quote": quote}

    @staticmethod
    def _build_arrival_context() -> str:
        now_local = datetime.datetime.now()
        parts = [f"it's {now_local.strftime('%H:%M')}, {PersonaAgent._build_base_context()}"]

        # Upcoming timed events today
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
                start = datetime.datetime.fromisoformat(start_str.replace('Z', '+00:00'))
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

        # Current outdoor weather
        try:
            location = get_default_location()
            weather_data = get_cached_or_fetch([location])
            city_data = weather_data.get(location)
            if city_data:
                precipitation = json.loads(city_data["hourly_precipitation"])
                temperatures = json.loads(city_data["hourly_temperatures"])
                first_time = datetime.datetime.fromisoformat(city_data["first_time"])
                idx = int((now_local - first_time).total_seconds() // 3600)
                idx = max(0, min(idx, len(temperatures) - 1))
                temp = temperatures[idx]
                precip = precipitation[idx]
                weather_key = PersonaAgent._classify(temp, precip)
                label = SITUATION_LABELS.get(weather_key, weather_key.replace('_', ' '))
                parts.append(f"it's {label} outside, {temp:.0f}°C")
        except Exception:
            pass

        return '; '.join(parts) if parts else "nothing specific to report"

    @classmethod
    def _generate_briefing(cls) -> str:
        cached = cls._quote_cache.get("welcome")
        if cached and time.time() - cached[1] < QUOTE_TTL:
            return cached[0]

        context = PersonaAgent._build_arrival_context()
        fallback = "Welcome home!"
        try:
            resp = requests.post(
                OLLAMA_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": (
                        "You are an anime girl on a home dashboard welcoming someone who just arrived home. "
                        "Give them a short warm briefing — speak directly to them. "
                        "Weave in one or two relevant facts from the context naturally. "
                        "Maximum 2 short sentences. Correct spelling and grammar. No text abbreviations. "
                        "If using a Japanese greeting, use the time-appropriate one: 'Ohayou' (morning), 'Konnichiwa' (daytime only), 'Konbanwa' (evening or night). "
                        "Examples: "
                        "'Welcome back! You've got a meeting at 3pm, and it's freezing outside — grab a coat.' / "
                        "'Oh, you're home! Nothing on the calendar today, and the weather's actually nice~' / "
                        "'Welcome back! Three meetings today — first one at 10am. Cold out there too.' "
                        f"Context: {context}. "
                        "Write the greeting now. Output only the lines, nothing else."
                    ),
                    "stream": False,
                },
                timeout=20,
            )
            quote = resp.json().get("response", "").strip().strip('"').strip("'")
            if quote:
                cls._quote_cache["welcome"] = (quote, time.time())
                return quote
        except Exception as e:
            print(f"[PersonaAgent] Briefing generation failed: {e}")

        cls._quote_cache["welcome"] = (fallback, time.time())
        return fallback

    @classmethod
    def _generate_quote(cls, state_key: str, situation: str, fallback: str) -> str:
        cached = cls._quote_cache.get(state_key)
        if cached and time.time() - cached[1] < QUOTE_TTL:
            return cached[0]
        try:
            resp = requests.post(
                OLLAMA_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": (
                        "You are writing a single casual line for an anime girl on a home dashboard. "
                        "She speaks in short, natural sentences — a little dramatic, occasionally trails off with '~' or '...' at the end of a sentence only, never mid-sentence. "
                        "Always use correct spelling and grammar. Never use text-speak abbreviations like u, ur, gonna, wanna, lol. "
                        "Never write inspirational or poetic phrasing. No metaphors. "
                        "Examples of the right tone: "
                        "'C-cold... why is it SO cold?!' / "
                        "'Hot chocolate weather. Definitely.' / "
                        "'The beach is calling my name~' / "
                        "'Snow! Beautiful. I'm still not going outside.' / "
                        "'Actually kind of nice out today~' / "
                        "'It is so quiet at this hour~' / "
                        "'Still going... the night feels endless.' "
                        f"Current situation: {situation}. Context: {PersonaAgent._build_base_context()}. "
                        "Be expressive and creative, but stay consistent with the situation — don't invent weather or events that contradict it. "
                        "If you mention a day or month, use the ones provided — never guess. Never quote the clock time directly. "
                        "Write one reaction in that same casual style, maximum 10 words. "
                        "Output only the line, nothing else."
                    ),
                    "stream": False,
                },
                timeout=10,
            )
            quote = resp.json().get("response", "").strip().strip('"').strip("'")
            if quote:
                cls._quote_cache[state_key] = (quote, time.time())
                return quote
        except Exception as e:
            print(f"[PersonaAgent] Ollama quote generation failed ({OLLAMA_URL}): {e}")
        cls._quote_cache[state_key] = (fallback, time.time())
        return fallback

    @staticmethod
    def _build_base_context() -> str:
        """Shared time/date context used by all Ollama calls: day, time, period, and weekday/weekend."""
        now = datetime.datetime.now()
        period = PersonaAgent._get_time_period()
        day_type = "weekend" if now.weekday() >= 5 else "weekday"
        if now.hour < 5:
            # Don't give a day name — "Monday night" implies Monday evening, "going into Monday" implies morning
            work_night = now.weekday() < 5  # Mon–Fri: need to be up for work
            night_type = "work night" if work_night else "weekend night"
            return f"{now.strftime('%B')}, it's {now.strftime('%H:%M')}, late {night_type}"
        return f"{now.strftime('%A')}, {now.strftime('%B')}, it's {now.strftime('%H:%M')} ({period}, {day_type})"

    @staticmethod
    def _get_time_period() -> str:
        hour = datetime.datetime.now().hour
        if 5 <= hour < 10:
            return "morning"
        if 10 <= hour < 18:
            return "day"
        if 18 <= hour < 22:
            return "evening"
        return "night"

    @staticmethod
    def _get_holiday_override() -> str | None:
        """Returns a holiday state key if today's all-day events match a known holiday, or None."""
        try:
            from services.google_calendar import get_all_events
            events = get_all_events()
            today = datetime.datetime.now().strftime('%Y-%m-%d')
            for event in events:
                event_date = event.get('start', {}).get('date')  # all-day events only
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
        """Returns 'in_meeting', 'meeting_soon', or None. Never raises."""
        try:
            from services.google_calendar import get_all_events
            events = get_all_events()
            now = datetime.datetime.now(datetime.timezone.utc)
            soon = now + datetime.timedelta(minutes=30)
            for event in events:
                start_str = event.get('start', {}).get('dateTime')
                end_str = event.get('end', {}).get('dateTime')
                if not start_str or not end_str:
                    continue  # skip all-day events
                start = datetime.datetime.fromisoformat(start_str.replace('Z', '+00:00'))
                end = datetime.datetime.fromisoformat(end_str.replace('Z', '+00:00'))
                if start <= now < end:
                    return "in_meeting"
                if now <= start <= soon:
                    return "meeting_soon"
        except Exception:
            pass
        return None

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
