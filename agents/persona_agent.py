import json
import datetime
import time
import requests

from services.weather_service import get_cached_or_fetch, get_default_location
from services.ollama_service import OLLAMA_BASE_URL, OLLAMA_MODEL
from agents.persona_states import STATES, TIME_PERIODS, CALENDAR_STATES, SITUATION_LABELS

OLLAMA_URL = f"{OLLAMA_BASE_URL}/api/generate"
QUOTE_TTL = 10 * 60  # seconds — regenerate on each persona refresh cycle


class PersonaAgent:
    _quote_cache: dict[str, tuple[str, float]] = {}  # state_key -> (quote, timestamp)

    @staticmethod
    def get_current_state() -> dict:
        # Calendar override (highest priority)
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
        situation = f"{weather_label} weather in the {period}"
        fallback = period_data["quote"] or base["quote"]
        quote = PersonaAgent._generate_quote(state_key, situation, fallback)

        return {"state": state_key, "prompt": prompt, "quote": quote}

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
                        "You are writing a single casual line of inner monologue for an anime girl on a home dashboard. "
                        "She speaks like a real person texting — short, unpolished, a little dramatic. "
                        "Never write inspirational or poetic phrasing. No metaphors. No 'just an excuse for'. "
                        "Examples of the right tone: "
                        "'C-cold... why is it SO cold?!' / "
                        "'Hot chocolate weather. Definitely.' / "
                        "'The beach is calling my name~' / "
                        "'Snow! Beautiful. I'm still not going outside.' "
                        f"Current situation: {situation}. "
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
    def _get_calendar_override() -> str | None:
        """Returns 'in_meeting', 'meeting_soon', or None. Never raises."""
        try:
            from services.google_calendar import get_all_events
            events = get_all_events()
            now = datetime.datetime.utcnow()
            soon = now + datetime.timedelta(minutes=30)
            for event in events:
                start_str = event.get('start', {}).get('dateTime')
                end_str = event.get('end', {}).get('dateTime')
                if not start_str or not end_str:
                    continue  # skip all-day events
                start = datetime.datetime.fromisoformat(start_str.replace('Z', '+00:00')).replace(tzinfo=None)
                end = datetime.datetime.fromisoformat(end_str.replace('Z', '+00:00')).replace(tzinfo=None)
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
