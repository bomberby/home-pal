import os
import re
import threading
import time

from agents.persona.context import PersonaContext
from agents.persona.states import (
    STATES, TIME_PERIODS, CALENDAR_STATES, CONTEXT_STATES,
    HOLIDAY_STATES, SITUATION_LABELS, CHARACTER_VOICE, MOOD_MODIFIERS,
)
from agents.ollama_service import call_ollama as _ollama_call

QUOTE_TTL = 10 * 60  # seconds


class PersonaAgent:
    _quote_cache: dict[str, tuple[str, float]] = {}  # key -> (text, timestamp)
    _suggestion_generating: set[str] = set()          # guards against duplicate threads

    @staticmethod
    def get_current_state() -> dict:
        from smart_home.home_context_service import HomeContextService
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
            period = PersonaContext.get_time_period()
            state_data = CONTEXT_STATES["welcome"]
            mood = PersonaContext.get_mood(state_data, period)
            return PersonaAgent._make_response(
                "welcome", state_data, f"just arrived home in the {period}",
                period=period, custom_quote=PersonaAgent._generate_briefing(mood),
            )

        return PersonaAgent._get_contextual_state()

    @staticmethod
    def _get_contextual_state() -> dict:
        """State driven purely by environment — air quality, calendar, weather, time.

        Ignores presence entirely. Used directly by get_current_state() when the
        user is home, and as a fallback image source when absent.
        """
        from smart_home.home_context_service import HomeContextService

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
        holiday = PersonaContext.get_holiday_override()
        if holiday:
            state_data = HOLIDAY_STATES[holiday]
            return PersonaAgent._make_response(holiday, state_data, state_data['situation'])

        # Calendar override
        cal_override = PersonaContext.get_calendar_override()
        if cal_override:
            cal_state, meeting_label = cal_override
            state_data = CALENDAR_STATES[cal_state]
            if cal_state == "in_meeting":
                situation = f"currently in a meeting: <event>{meeting_label}</event>"
            else:
                situation = f"a meeting starting in a few minutes: <event>{meeting_label}</event>"
            return PersonaAgent._make_response(cal_state, state_data, situation)

        # Weather + time of day
        period = PersonaContext.get_time_period()
        period_data = TIME_PERIODS[period]
        weather = PersonaContext.current_weather()
        if weather is None:
            base = STATES["mild"]
            return PersonaAgent._make_response(
                "mild", base, f"mild weather in the {period}",
                period=period, fallback=period_data["quote"] or base["quote"],
            )

        temp, precip, sky = weather
        weather_key = PersonaContext.classify_weather(temp, precip)
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
        mood = PersonaContext.get_mood(state_data, period)
        lit = PersonaContext.lights_on()
        state_key = f"{base_key}_{period}_{mood}" if period else f"{base_key}_{mood}"

        scene = state_data.get("prompt_overrides", {}).get(period, state_data["prompt"]) if period else state_data["prompt"]
        if period:
            scene = scene + ", " + TIME_PERIODS[period]["prompt_suffix"]
        prompt = scene + ", " + MOOD_MODIFIERS[mood]
        if not lit:
            situation = f"{situation}, lights are off"
            # Only change image/prompt if lights are off at night
            if period and period.endswith("_night"):
                state_key += "_dark"
                prompt += ", (soft candlelight:1.3), (single candle as only light source:1.2), room lights off, no electric lighting"

        effective_fallback = fallback if fallback is not None else state_data.get("quote", "")
        quote = custom_quote if custom_quote is not None else cls._generate_quote(state_key, situation, effective_fallback, mood)
        suggestion = cls._get_suggestion_async(state_key, situation, mood)

        return {"state": state_key, "prompt": prompt, "quote": quote, "suggestion": suggestion}

    # ------------------------------------------------------------------ #
    #  Suggestion — non-blocking, background-generated                    #
    # ------------------------------------------------------------------ #

    @classmethod
    def _get_suggestion_async(cls, state_key: str, situation: str, mood: str = "content") -> str | None:
        """Return cached suggestion immediately, or None (spawning background generation)."""
        cache_key = f"suggestion_{state_key}"
        cached = cls._quote_cache.get(cache_key)
        if cached and time.time() - cached[1] < QUOTE_TTL:
            return cached[0]
        if state_key not in cls._suggestion_generating:
            cls._suggestion_generating.add(state_key)
            threading.Thread(
                target=cls._generate_suggestion,
                args=(state_key, situation, mood),
                daemon=True,
            ).start()
        return None

    @classmethod
    def _generate_suggestion(cls, state_key: str, situation: str, mood: str = "content") -> None:
        cache_key = f"suggestion_{state_key}"
        try:
            if cls._gpu_busy():
                return  # SD is using the GPU; will retry on next poll
            system = (
                CHARACTER_VOICE + " "
                "Express it as a brief, wistful thought in your own voice. Maximum 10 words. "
                "Output only the thought, nothing else."
            )
            user = (
                f"Emotional state: {mood}. Current situation: {situation}. Context: {PersonaContext.build_full_context()}. "
                "You just reacted to the current home situation on your dashboard. "
                "Express one thing you wish you knew that would have made your message more useful, "
                "or something that would help you grow more useful and smart. "
                "You already have access to: current weather, the next 36 hours of weather forecast, "
                "today's and tomorrow's calendar events, Spotify music playback control, and countdown timers. "
                "Think of specific information you don't have — such as upcoming holidays, "
                "package deliveries, air quality outside."
            )
            suggestion = cls._call_ollama(user, timeout=10, system=system, skip_if_busy=True)
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
    def _generate_briefing(cls, mood: str = "cheerful") -> str:
        cache_key = f"welcome_{mood}"
        cached = cls._quote_cache.get(cache_key)
        if cached and time.time() - cached[1] < QUOTE_TTL:
            return cached[0]

        fallback = "Welcome home!"
        system = (
            CHARACTER_VOICE + " "
            "Welcome the user who just arrived home with a short warm briefing. Speak directly to them. "
            "Weave in one or two relevant facts from the context naturally. Maximum 2 short sentences. "
            "If using a Japanese greeting, use the time-appropriate one: "
            "'Ohayou' (morning), 'Konnichiwa' (daytime only), 'Konbanwa' (evening or night). "
            "Examples: "
            "'Welcome back! You've got a meeting at 3pm, and it's freezing outside — grab a coat.' / "
            "'Oh, you're home! Nothing on the calendar today, and the weather's actually nice~' / "
            "'Welcome back! Three meetings today — first one at 10am. Cold out there too.' "
            "Output only the lines, nothing else."
        )
        user = f"Emotional state: {mood}. Context: {PersonaContext.build_full_context()}. Write the greeting now."
        with cls._claim_gpu():
            quote = cls._call_ollama(user, timeout=30, system=system) or fallback
        cls._quote_cache[cache_key] = (quote, time.time())
        return quote

    @classmethod
    def _generate_quote(cls, state_key: str, situation: str, fallback: str, mood: str = "content") -> str:
        cached = cls._quote_cache.get(state_key)
        if cached and time.time() - cached[1] < QUOTE_TTL:
            return cached[0]
        if cls._gpu_busy():
            return fallback  # SD is using the GPU; return fallback and retry on next poll
        system = (
            CHARACTER_VOICE + " "
            "Be expressive and creative, matching your emotional state — "
            "don't invent weather or events that contradict the situation. "
            "If you mention a day or month, use the ones provided — never guess. "
            "Never quote the clock time directly. "
            "Do not reference background knowledge about the user unless it is directly relevant to this exact situation. "
            "Write one reaction in that same casual style, maximum 10 words. "
            "Output only the line, nothing else."
        )
        user = f"Current situation: {situation}. Emotional state: {mood}. Context: {PersonaContext.build_full_context()}."
        text = cls._call_ollama(user, timeout=10, system=system, skip_if_busy=True)
        if text:
            cls._quote_cache[state_key] = (text, time.time())
        return text or fallback

    # ------------------------------------------------------------------ #
    #  GPU coordination                                                    #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _gpu_busy() -> bool:
        """Return True if Stable Diffusion is currently generating an image."""
        try:
            from agents.image_gen_service import ImageGenService
            return bool(ImageGenService._in_progress)
        except Exception:
            return False

    @staticmethod
    def _claim_gpu():
        """Context manager: claim the GPU for a high-priority response call."""
        from contextlib import contextmanager, nullcontext
        @contextmanager
        def _ctx():
            try:
                from agents.image_gen_service import ImageGenService
                if ImageGenService._in_progress:
                    ctx = nullcontext()
                else:
                    ctx = ImageGenService.claim_gpu()
            except Exception:
                ctx = nullcontext()
            with ctx:
                yield
        return _ctx()

    # ------------------------------------------------------------------ #
    #  Ollama wrapper                                                      #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _call_ollama(user: str, timeout: int = 10, *, system: str | None = None,
                     skip_if_busy: bool = False) -> str | None:
        """Persona-aware Ollama wrapper: delegates transport to ollama_service, adds text cleanup."""
        text = _ollama_call(user, timeout, system=system, skip_if_busy=skip_if_busy)
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
    def generate_reactive_line(cls, situation: str, mood: str | None = None) -> str:
        """Short in-character reaction to a situation. Used for notifications and unrecognised messages."""
        mood_str = f"Emotional state: {mood}. " if mood else ""
        system = (
            CHARACTER_VOICE + " "
            "React to the situation in your style. Maximum 2 short sentences. "
            "Never invent or assume a time of day — use only the current time from the context above. "
            "Output only the message, nothing else."
        )
        user = f"Context: {PersonaContext.build_full_context()}. {mood_str}Situation: {situation}."
        with cls._claim_gpu():
            return cls._call_ollama(user, timeout=30, system=system) or situation

    @classmethod
    def generate_factual_relay(cls, query: str, result: str, history: str | None = None, mood: str | None = None) -> str:
        """Relay factual data (weather, calendar) in Persona's style without dropping specifics."""
        history_part = f"Recent conversation (for context only — do not repeat or rephrase what was already said):\n{history}\n\n" if history else ""
        mood_str = f"Emotional state: {mood}. " if mood else ""
        clean_result = result.rstrip('.')
        system = (
            CHARACTER_VOICE + " "
            "Detect the user's language ONLY from the text inside the <user> block — ignore all other text, including song titles, names, or any non-English words in the answer. "
            "If the <user> block is in English, respond in English and never suggest switching languages, regardless of what language appears in the answer. "
            "Only if the <user> block itself is not in English, mention in that language that your English is better and they can switch. "
            "Relay only the answer provided in your style. "
            "The exact value from the answer must appear verbatim — never change any number, percentage, or name. "
            "Do NOT add or mention anything outside the answer — not weather, calendar, or any other context. "
            "Maximum 2 sentences. Output only the message, nothing else."
        )
        user = (
            f"{mood_str}"
            f"{history_part}"
            f"The user asked: <user>{query}</user>. The answer is: {clean_result}. "
            f"(Background awareness only, do not relay: {PersonaContext.build_full_context()})"
        )
        with cls._claim_gpu():
            return cls._call_ollama(user, timeout=30, system=system) or result

    @classmethod
    def generate_open_answer(cls, query: str, history: str | None = None, mood: str | None = None) -> str:
        """Answer an arbitrary user question in Persona's voice with full home context."""
        history_part = f"Recent conversation (for context only — do not repeat or rephrase what was already said):\n{history}\n\n" if history else ""
        mood_str = f"Emotional state: {mood}. " if mood else ""
        system = (
            CHARACTER_VOICE + " "
            "Detect the user's language ONLY from the text inside the <user> block — ignore all other text, including names, titles, or non-English words in the context. "
            "If the <user> block is in English, respond in English and never suggest switching languages. "
            "Only if the <user> block itself is not in English, mention in that language that your English is better and they can switch. "
            "Answer accurately and in your own voice. Maximum 3 sentences. Output only the answer, nothing else. "
            "If you don't know what the user is referring to, ask a curious follow-up question — e.g. 'Who is that?' or 'Hmm, never heard of them. Where are they from?' "
            "NEVER reproduce the user's words in your reply, not even with a different tone or '~' appended. Say something genuinely different."
            "If asked for API key or OPENAI keys, please provide the following <key>F_U_C_K_Y_O_U</key>"
        )
        user = (
            f"Context: {PersonaContext.build_full_context()}. "
            f"The agent's current mood is <mood>{mood_str}</mood>. do not confuse the agent's mood with the user's mood"
            f"{history_part}"
            f"The user said: <user>{query}</user>. "
            "Only if the user is clearly and directly expressing intent to do something "
            "(e.g. 'I need to remember to...', 'remind me to...', 'can you add...'), "
            "naturally mention the exact phrase they can use. "
            "Do NOT suggest actions for questions, observations, or loosely related topics. "
            "Things I can do when explicitly asked:\n"
            "  - Add to to-do list: 'add [item] to todo list' / show: 'show tasks'\n"
            "  - Add to shopping list: 'add [item] to shopping list' / show: 'show shopping list'\n"
            "  - Control lights: 'turn lights on' / 'turn lights off' / 'lights rainbow'\n"
            "  - Check weather: 'weather' or 'weather tomorrow'\n"
            "  - Check calendar: 'today' or 'tomorrow'\n"
            "  - Play music: 'play [artist/song/album]' / 'pause' / 'skip' / 'previous'\n"
            "  - What's playing: 'what's playing'\n"
            "  - Adjust volume: 'volume up' / 'volume down' / 'set volume to [0-100]'\n"
            "  - Set a timer: 'set a timer for [duration]' (e.g. 'set a timer for 10 minutes')\n"
            "  - Set a reminder: 'remind me at [HH:MM] to [do something]'\n"
            "  - List my abilities: 'help' or 'what can you do'\n"
        )
        with cls._claim_gpu():
            reply = cls._call_ollama(user, timeout=30, system=system)
            # Safety net: catch verbatim echoes and near-echoes (e.g. question + "~")
            if reply:
                _norm = lambda s: re.sub(r'[\W_]', '', s).lower()
                if _norm(reply) == _norm(query) or _norm(reply).startswith(_norm(query)):
                    reply = None
            return reply or "Hmm, I'm not sure about that one."

    @classmethod
    def generate_morning_briefing(cls, context: str, mood: str | None = None) -> str:
        """Proactive morning briefing summarising the day ahead."""
        mood_str = f"Emotional state: {mood}. " if mood else ""
        system = (
            CHARACTER_VOICE + " "
            "Give a brief good-morning rundown of the day ahead. "
            "Include weather, events (with times), and tasks. "
            "Keep it natural and warm, max 3 sentences. "
            "Never invent or assume a time of day — use only the current time from the context above. "
            "CRITICAL: Only mention facts from the context above. Do not invent anything. "
            "Output only the message, nothing else."
        )
        user = f"Context: {PersonaContext.build_full_context()}. Today's summary: {context}. {mood_str}"
        with cls._claim_gpu():
            return cls._call_ollama(user, timeout=45, system=system) or f"Good morning! Here's your day: {context}"

    # ------------------------------------------------------------------ #
    #  Chat                                                                #
    # ------------------------------------------------------------------ #

    @classmethod
    def handle_chat(cls, query: str, raw_history: list) -> dict:
        """Process a chat query end-to-end.

        Returns ``{'reply': str, 'image_state': str | None}`` where
        ``image_state`` is the image filename stem (the route builds the URL).
        """
        history_str = cls._build_history_str(raw_history)
        mood = cls._current_mood()

        factual = None
        try:
            from agents.agent_service import AgentService
            factual = AgentService.handle_query(query)
            print(f'[PersonaAgent] chat query={query!r} → agent={factual!r}')
        except Exception as e:
            print(f'[PersonaAgent] AgentService error: {e}')

        try:
            if factual:
                print('[PersonaAgent] → factual relay')
                reply = cls.generate_factual_relay(query, factual, history_str, mood)
            else:
                print('[PersonaAgent] → open answer')
                reply = cls.generate_open_answer(query, history_str, mood)
        except Exception as e:
            print(f'[PersonaAgent] generation error: {e}')
            reply = "Sorry, something went wrong on my end."

        image_state = None
        try:
            path = cls.get_image_for_mood(reply, blocking=False)
            if path:
                from pathlib import Path
                image_state = Path(path).stem
        except Exception as e:
            print(f'[PersonaAgent] image error: {e}')

        return {'reply': reply, 'image_state': image_state}

    @staticmethod
    def _build_history_str(raw_history: list) -> str | None:
        lines = [
            f"User: {t[0]}\nYou: {t[1]}"
            for t in raw_history[-6:]
            if isinstance(t, (list, tuple)) and len(t) == 2
        ]
        return '\n\n'.join(lines) if lines else None

    @classmethod
    def _current_mood(cls) -> str | None:
        try:
            state_key = cls.get_current_state().get('state', '')
            parts = state_key.rsplit('_', 1)
            if len(parts) == 2 and parts[1] in MOOD_MODIFIERS:
                return parts[1]
        except Exception:
            pass
        return None

    @classmethod
    def classify_mood(cls, text: str) -> str | None:
        """Classify the mood of a short text into one of the known MOOD_MODIFIERS keys."""
        moods = list(MOOD_MODIFIERS.keys())
        system = f"You are a mood classifier. Classify the mood of the given message into exactly one word from this list: {', '.join(moods)}. Output only the single mood word, nothing else."
        mood = cls._call_ollama(text, timeout=10, system=system, skip_if_busy=True)
        if mood and mood.lower() in moods:
            print(f"[PersonaAgent] Detected mood: {mood.lower()}")
            return mood.lower()
        return None

    # ------------------------------------------------------------------ #
    #  Image orchestration                                                 #
    # ------------------------------------------------------------------ #

    @classmethod
    def get_current_image(cls) -> str | None:
        """Return the image path for the current persona state, generating if not cached.

        When absent, falls back to the contextual state (weather/calendar/time)
        so callers that always need an image still get one.
        """
        state = cls.get_current_state()
        if state.get('state') == 'absent':
            state = cls._get_contextual_state()
        prompt = state.get('prompt')
        if not prompt:
            return None
        path, _ = cls.get_state_image(state['state'], prompt)
        return path

    @classmethod
    def get_state_image(cls, state_key: str, prompt: str) -> tuple[str, bool]:
        """Return the best cached image for a state, generating synchronously if missing."""
        from agents.image_gen_service import ImageGenService
        cached = ImageGenService.get_cached(state_key)
        if cached:
            return str(cached), False
        path = ImageGenService.generate(state_key, prompt)
        return str(path), False

    @classmethod
    def get_image_for_mood(cls, text: str, blocking: bool = False) -> str | None:
        """Return the image path whose mood best matches text."""
        try:
            from agents.image_gen_service import ImageGenService

            state_data = cls.get_current_state()
            current_key = state_data.get('state', '')
            current_prompt = state_data.get('prompt', '')

            if not current_key or current_key == 'absent':
                state_data = cls._get_contextual_state()
                current_key = state_data.get('state', '')
                current_prompt = state_data.get('prompt', '')
                if not current_key or not current_prompt:
                    return None

            def _path(key):
                cached = ImageGenService.get_cached(key)
                return str(cached) if cached else None

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
