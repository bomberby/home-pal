import os
import re
import datetime
import time
import threading

from services.weather_service import get_default_location, get_hourly_forecast
from services.ollama_service import call_ollama as _ollama_call
from services.calendar_utils import parse_dt, event_label
from agents.persona_states import (
    STATES, TIME_PERIODS, CALENDAR_STATES, CONTEXT_STATES,
    HOLIDAY_PATTERNS, HOLIDAY_STATES, SITUATION_LABELS,
    CHARACTER_VOICE, MOOD_MODIFIERS,
)

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
            voc = HomeContextService._voc
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
        cal_override = PersonaAgent._get_calendar_override()
        if cal_override:
            cal_state, meeting_label = cal_override
            state_data = CALENDAR_STATES[cal_state]
            if cal_state == "in_meeting":
                situation = f"currently in a meeting: <event>{meeting_label}</event>"
            else:
                situation = f"a meeting starting in a few minutes: <event>{meeting_label}</event>"
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

        temp, precip, sky = weather
        weather_key = PersonaAgent._classify(temp, precip)
        base = STATES[weather_key]
        weather_label = SITUATION_LABELS.get(weather_key, weather_key.replace("_", " "))
        sky_str = f", {sky}" if sky else ""
        situation = f"{weather_label} weather ({temp:.0f}°C{sky_str}) in the {period}"

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
        lit = cls._lights_on()
        state_key = f"{base_key}_{period}_{mood}" if period else f"{base_key}_{mood}"

        scene = state_data.get("prompt_overrides", {}).get(period, state_data["prompt"]) if period else state_data["prompt"]
        if period:
            scene = scene + ", " + TIME_PERIODS[period]["prompt_suffix"]
        prompt = scene + ", " + MOOD_MODIFIERS[mood]
        if not lit:
            situation = f"{situation}, lights are off"
            # Only change image/prompt if lights are off at night
            if period.endswith("_night"): 
                state_key += "_dark"
                prompt += ", (soft candlelight:1.3), (single candle as only light source:1.2), room lights off, no electric lighting"

        effective_fallback = fallback if fallback is not None else state_data.get("quote", "")
        quote = custom_quote if custom_quote is not None else cls._generate_quote(state_key, situation, effective_fallback, mood)
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
                "Now express one thing you wish you knew that would have made your message more useful. "
                "Also something that will help you grow more, and become more prodcutive or smart would be good."
                "You already have access to: current weather, the next 36 hours of weather forecast, "
                "today's and tomorrow's calendar events. "
                "Think of specific information you don't have — such as upcoming holidays, "
                "package deliveries, air quality outside. "
                "Express it as a brief, wistful thought in your own voice. Maximum 10 words. "
                f"Current situation: {situation}. Context: {cls._build_full_context()}. "
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
            f"Context: {cls._build_full_context()}. Write the greeting now. Output only the lines, nothing else."
        )
        quote = cls._call_ollama(prompt, timeout=20) or fallback
        cls._quote_cache["welcome"] = (quote, time.time())
        return quote

    @classmethod
    def _generate_quote(cls, state_key: str, situation: str, fallback: str, mood: str = "content") -> str:
        cached = cls._quote_cache.get(state_key)
        if cached and time.time() - cached[1] < QUOTE_TTL:
            return cached[0]
        prompt = (
            CHARACTER_VOICE + " "
            f"Current situation: {situation}. Emotional state: {mood}. Context: {cls._build_full_context()}. "
            "Be expressive and creative, matching your emotional state — "
            "don't invent weather or events that contradict the situation. "
            "If you mention a day or month, use the ones provided — never guess. "
            "Never quote the clock time directly. "
            "Do not reference background knowledge about the user unless it is directly relevant to this exact situation. "
            "Write one reaction in that same casual style, maximum 10 words. "
            "Output only the line, nothing else."
        )
        quote = cls._call_ollama(prompt, timeout=10) or fallback
        cls._quote_cache[state_key] = (quote, time.time())
        return quote

    @staticmethod
    def _call_ollama(prompt: str, timeout: int = 10) -> str | None:
        """Persona-aware Ollama wrapper: delegates transport to ollama_service, adds text cleanup."""
        text = _ollama_call(prompt, timeout)
        if not text:
            return None
        text = text.strip('"').strip("'")
        # ~ after ellipsis is nonsensical — remove it
        text = re.sub(r'(\.+|…)\s*~', r'\1', text)
        return text or None

    # ------------------------------------------------------------------ #
    #  Telegram / notification text generation                             #
    # ------------------------------------------------------------------ #

    @classmethod
    def generate_reactive_line(cls, situation: str) -> str:
        """Short in-character reaction to a situation. Used for notifications and unrecognised messages."""
        prompt = (
            CHARACTER_VOICE + " "
            f"Context: {cls._build_full_context()}. "
            f"Situation: {situation}. "
            "React to the situation in your style. Maximum 2 short sentences. "
            "Never invent or assume a time of day — use only the current time from the context above. "
            "Output only the message, nothing else."
        )
        return cls._call_ollama(prompt, timeout=15) or situation

    @classmethod
    def generate_factual_relay(cls, query: str, result: str, history: str | None = None) -> str:
        """Relay factual data (weather, calendar) in Persona's style without dropping specifics."""
        history_part = f"Recent conversation (for context only — do not repeat or rephrase what was already said):\n{history}\n\n" if history else ""
        prompt = (
            CHARACTER_VOICE + " "
            f"{history_part}"
            f"The user asked: <user>{query}</user>. The answer is: {result}. "
            "Respond in the same language the user used. "
            "If that language is not English, also mention in that language that your English is better and they can switch. "
            "Relay only the answer above in your style. "
            "Keep specific numbers and names accurate (temperatures with °C, meeting names, times). "
            "Do NOT add or mention anything outside the answer — not weather, calendar, or any other context. "
            "Maximum 2 sentences. Output only the message, nothing else. "
            f"(Background awareness only, do not repeat: {cls._build_full_context()})"
        )
        return cls._call_ollama(prompt, timeout=15) or result

    @classmethod
    def generate_open_answer(cls, query: str, history: str | None = None) -> str:
        """Answer an arbitrary user question in Persona's voice with full home context.

        If the message sounds like something the agent can act on, hints at the
        exact phrase the user can say rather than silently ignoring the intent.
        """
        history_part = f"Recent conversation (for context only — do not repeat or rephrase what was already said):\n{history}\n\n" if history else ""
        prompt = (
            CHARACTER_VOICE + " "
            f"Context: {cls._build_full_context()}. "
            f"{history_part}"
            f"The user said: <user>{query}</user>. "
            "Respond in the same language the user used. "
            "If that language is not English, also mention in that language that your English is better and they can switch. "
            "Answer accurately and in your own voice. "
            "Only if the user is clearly and directly expressing intent to do something "
            "(e.g. 'I need to remember to...', 'remind me to...', 'can you add...'), "
            "naturally mention the exact phrase they can use. "
            "Do NOT suggest actions for questions, observations, or loosely related topics. "
            "Things I can do when explicitly asked:\n"
            "  - Add to to-do list: 'add [item] to todo list'\n"
            "  - Add to shopping list: 'add [item] to shopping list'\n"
            "  - Control lights: 'turn lights on' / 'turn lights off'\n"
            "  - Check weather: 'weather' or 'weather tomorrow'\n"
            "  - Check calendar: 'today' or 'tomorrow'\n"
            "Maximum 2 sentences. Output only the answer, nothing else."
        )
        return cls._call_ollama(prompt, timeout=15) or "Hmm, I'm not sure about that one."

    @classmethod
    def generate_morning_briefing(cls, context: str) -> str:
        """Proactive morning briefing summarising the day ahead."""
        prompt = (
            CHARACTER_VOICE + " "
            f"Context: {cls._build_full_context()}. Today's summary: {context}. "
            "Give a brief good-morning rundown of the day ahead. "
            "Include weather, events (with times), and tasks. "
            "Keep it natural and warm, max 3 sentences. "
            "Never invent or assume a time of day — use only the current time from the context above. "
            "CRITICAL: Only mention facts from the context above. Do not invent anything. "
            "Output only the message, nothing else."
        )
        return cls._call_ollama(prompt, timeout=20) or f"Good morning! Here's your day: {context}"

    @classmethod
    def classify_mood(cls, text: str) -> str | None:
        """Classify the mood of a short text into one of the known MOOD_MODIFIERS keys."""
        moods = list(MOOD_MODIFIERS.keys())
        prompt = (
            f"Classify the mood of this message into exactly one word from this list: {', '.join(moods)}.\n"
            f"Message: \"{text}\"\n"
            "Output only the single mood word, nothing else."
        )
        mood = cls._call_ollama(prompt, timeout=5)
        if mood and mood.lower() in moods:
            print(f"[PersonaAgent] Detected mood: {mood.lower()}")
            return mood.lower()
        return None

    @classmethod
    def get_state_image(cls, state_key: str, prompt: str) -> tuple[str | None, bool]:
        """Get the cached image for a state, triggering background generation if missing.

        Returns (path, is_generating). Path is None when generation is in progress.
        """
        from services.image_gen_service import ImageGenService
        cached = ImageGenService.get_cached(state_key)
        if cached:
            return str(cached), False
        if state_key not in ImageGenService._in_progress:
            threading.Thread(
                target=ImageGenService.generate,
                args=(state_key, prompt),
                daemon=True,
            ).start()
        return None, True

    @classmethod
    def get_image_for_mood(cls, text: str, blocking: bool = False) -> str | None:
        """Return the image path whose mood best matches text.

        Classifies the mood of text, swaps the mood segment of the current
        state key, and returns that image. Generates it if missing.
        Falls back to the current state image if anything fails.

        blocking: if True, waits for generation before returning.
                  if False, spawns a background thread and returns the
                  current state image as an immediate fallback.
        """
        try:
            from services.image_gen_service import ImageGenService

            state_data = cls.get_current_state()
            current_key = state_data.get('state', '')
            current_prompt = state_data.get('prompt', '')

            if not current_key or current_key == 'absent':
                return None

            def _path(key):
                p = os.path.join('tmp', 'persona', f'{key}.png')
                return p if os.path.exists(p) else None

            known_moods = set(MOOD_MODIFIERS.keys())
            parts = current_key.rsplit('_', 1)
            if len(parts) != 2 or parts[1] not in known_moods:
                return _path(current_key)

            base_key, current_mood = parts[0], parts[1]

            detected_mood = cls.classify_mood(text)
            if not detected_mood or detected_mood == current_mood:
                return _path(current_key)

            new_key = f"{base_key}_{detected_mood}"
            cached = ImageGenService.get_cached(new_key)
            if cached:
                print(f"[PersonaAgent] Mood matched image: {new_key}")
                return str(cached)

            if new_key not in ImageGenService._in_progress and current_prompt:
                old_suffix = f", {MOOD_MODIFIERS[current_mood]}"
                if current_prompt.endswith(old_suffix):
                    new_prompt = current_prompt[:-len(old_suffix)] + f", {MOOD_MODIFIERS[detected_mood]}"
                    if blocking:
                        print(f"[PersonaAgent] Generating mood image (blocking): {new_key}")
                        ImageGenService.generate(new_key, new_prompt)
                        return _path(new_key)
                    else:
                        print(f"[PersonaAgent] Triggering mood image generation (background): {new_key}")
                        threading.Thread(
                            target=ImageGenService.generate,
                            args=(new_key, new_prompt),
                            daemon=True,
                        ).start()

            return _path(current_key)  # fall back while generating

        except Exception as e:
            print(f"[PersonaAgent] Image mood match failed: {e}")
            return None

    # ------------------------------------------------------------------ #
    #  Context helpers                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_full_context() -> str:
        """Combined context for all generation prompts: time/day/calendar/weather + persona memory."""
        from services.memory_service import MemoryService
        base = PersonaAgent._build_base_context()
        mem = MemoryService.format_for_prompt()
        return f"{base}. {mem}" if mem else base

    @staticmethod
    def _build_base_context() -> str:
        """Full context for all generation prompts: time, calendar, and current weather."""
        now = datetime.datetime.now()
        period = PersonaAgent._get_time_period()
        day_type = "weekend" if now.weekday() >= 5 else "weekday"
        if period == "late_night":
            work_night = now.weekday() < 5
            night_type = "work night" if work_night else "weekend night"
            parts = [f"{now.strftime('%A')}, {now.strftime('%B')}, it's {now.strftime('%H:%M')} (24h clock), late {night_type}"]
        else:
            parts = [f"{now.strftime('%A')}, {now.strftime('%B')}, it's {now.strftime('%H:%M')} (24h clock, {period}, {day_type})"]

        cal = PersonaAgent._build_calendar_context()
        if cal:
            parts.append(cal)

        weather = PersonaAgent._current_weather()
        if weather:
            temp, precip, sky = weather
            weather_key = PersonaAgent._classify(temp, precip)
            label = SITUATION_LABELS.get(weather_key, weather_key.replace('_', ' '))
            sky_str = f", {sky}" if sky else ""
            parts.append(f"outside: {label}, {temp:.0f}°C{sky_str}")

        parts.append("lights are on" if PersonaAgent._lights_on() else "lights are off")

        return "; ".join(parts)

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
                    today_remaining.append((start, event_label(event)))
                elif start_local_date == tomorrow_str:
                    tomorrow_events.append((start, event_label(event)))

            today_remaining.sort(key=lambda x: x[0])
            tomorrow_events.sort(key=lambda x: x[0])

            parts = []
            if today_remaining:
                t = today_remaining[0][0].astimezone().strftime('%H:%M')
                count = len(today_remaining)
                label = f"<event>{today_remaining[0][1]}</event> at {t}" if count == 1 else f"{count} events left today, next <event>{today_remaining[0][1]}</event> at {t}"
                parts.append(label)
            else:
                parts.append("no meetings left today")
            if tomorrow_events:
                t = tomorrow_events[0][0].astimezone().strftime('%H:%M')
                count = len(tomorrow_events)
                label = f"tomorrow: <event>{tomorrow_events[0][1]}</event> at {t}" if count == 1 else f"tomorrow: {count} events, first <event>{tomorrow_events[0][1]}</event> at {t}"
                parts.append(label)

            return '; '.join(parts)
        except Exception:
            return None

    @staticmethod
    def _get_time_period() -> str:
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
    def _get_calendar_override() -> tuple[str, str] | None:
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
    def _current_weather() -> tuple[float, float, str] | None:
        """Return (temp, precip, sky_description) for the current hour, or None if unavailable."""
        forecast = get_hourly_forecast(get_default_location(), count=1)
        if not forecast or not forecast['temps']:
            return None
        return (
            forecast['temps'][0],
            forecast['precips'][0],
            forecast['condition_descriptions'][0],
        )

    @staticmethod
    def _get_mood(state_data: dict, period: str | None = None) -> str:
        """Resolve mood: period override → base mood → fallback 'content'."""
        if period:
            override = state_data.get("mood_overrides", {}).get(period)
            if override:
                return override
        return state_data.get("mood", "content")

    @staticmethod
    def _lights_on() -> bool:
        try:
            from smart_home.smart_home_service import get_device
            return get_device('led').activated
        except Exception:
            return True  # assume on if unknown

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
