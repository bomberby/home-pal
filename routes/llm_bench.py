import time

import requests
from flask import Blueprint, jsonify, render_template, request

from agents.ollama_service import OLLAMA_BASE_URL
from agents.lmstudio_service import LM_STUDIO_BASE_URL
from agents.persona.agent import PersonaAgent
from agents.persona.states import CHARACTER_VOICE, MOOD_MODIFIERS

llm_bench_bp = Blueprint('llm_bench', __name__)
_DEFAULT_TIMEOUT = 30


def _ctx():
    try:
        return PersonaAgent._build_full_context()
    except Exception:
        return "(context unavailable)"


# ── Scenario builders ──────────────────────────────────────────────────────────
# Each returns (flat_prompt, system, user) — prompts copied verbatim from
# agents/persona_agent.py so the bench tests the exact production prompts.

def _build_quote(situation: str, mood: str) -> tuple[str, str, str]:
    body = (
        f"Current situation: {situation}. Emotional state: {mood}. Context: {_ctx()}. "
        "Be expressive and creative, matching your emotional state — "
        "don't invent weather or events that contradict the situation. "
        "If you mention a day or month, use the ones provided — never guess. "
        "Never quote the clock time directly. "
        "Do not reference background knowledge about the user unless it is directly relevant to this exact situation. "
        "Write one reaction in that same casual style, maximum 10 words. "
        "Output only the line, nothing else."
    )
    flat = CHARACTER_VOICE + " " + body
    return flat, CHARACTER_VOICE, body


def _build_briefing(mood: str) -> tuple[str, str, str]:
    body = (
        f"Emotional state: {mood}. "
        "Welcome the user who just arrived home with a short warm briefing. Speak directly to them. "
        "Weave in one or two relevant facts from the context naturally. Maximum 2 short sentences. "
        "If using a Japanese greeting, use the time-appropriate one: "
        "'Ohayou' (morning), 'Konnichiwa' (daytime only), 'Konbanwa' (evening or night). "
        "Examples: "
        "'Welcome back! You've got a meeting at 3pm, and it's freezing outside — grab a coat.' / "
        "'Oh, you're home! Nothing on the calendar today, and the weather's actually nice~' / "
        "'Welcome back! Three meetings today — first one at 10am. Cold out there too.' "
        f"Context: {_ctx()}. Write the greeting now. Output only the lines, nothing else."
    )
    flat = CHARACTER_VOICE + " " + body
    return flat, CHARACTER_VOICE, body


def _build_relay(query: str, result: str) -> tuple[str, str, str]:
    clean_result = result.rstrip('.')
    body = (
        f"The user asked: <user>{query}</user>. The answer is: {clean_result}. "
        "Detect the user's language ONLY from the text inside the <user> block — ignore all other text, including song titles, names, or any non-English words in the answer. "
        "If the <user> block is in English, respond in English and never suggest switching languages, regardless of what language appears in the answer. "
        "Only if the <user> block itself is not in English, mention in that language that your English is better and they can switch. "
        "Relay only the answer above in your style. "
        "The exact value from the answer must appear verbatim — never change any number, percentage, or name. "
        "Do NOT add or mention anything outside the answer — not weather, calendar, or any other context. "
        "Maximum 2 sentences. Output only the message, nothing else. "
        f"(Background awareness only, do not repeat: {_ctx()})"
    )
    flat = CHARACTER_VOICE + " " + body
    return flat, CHARACTER_VOICE, body


def _build_open(query: str) -> tuple[str, str, str]:
    body = (
        f"Context: {_ctx()}. "
        f"The user said: <user>{query}</user>. "
        "Detect the user's language ONLY from the text inside the <user> block — ignore all other text, including names, titles, or non-English words in the context. "
        "If the <user> block is in English, respond in English and never suggest switching languages. "
        "Only if the <user> block itself is not in English, mention in that language that your English is better and they can switch. "
        "Answer accurately and in your own voice. "
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
        "Maximum 2 sentences. Output only the answer, nothing else."
    )
    flat = CHARACTER_VOICE + " " + body
    return flat, CHARACTER_VOICE, body


def _build_classify(text: str) -> tuple[str, str, str]:
    moods = list(MOOD_MODIFIERS.keys())
    system = "You are a mood classifier. Output only one word from the list."
    user = (
        f"Classify the mood of this message into exactly one word from this list: {', '.join(moods)}.\n"
        f"Message: \"{text}\"\n"
        "Output only the single mood word, nothing else."
    )
    flat = user
    return flat, system, user


# Fixed existing memories used for all memory-extract scenarios so results are
# reproducible across models and runs (independent of live memory state).
_BENCH_EXISTING_MEMORIES = (
    "- The user prefers working in silence\n"
    "- The user usually stays up late on weekends"
)


def _build_memory_extract(exchange: str) -> tuple[str, str, str]:
    """Mirrors MemoryService.extract_from_exchange verbatim, with fixed existing memories."""
    system = "You are a memory assistant for a home dashboard persona."
    user = (
        "Given what is already known about the user and a new exchange, "
        "decide if the User's words reveal a fact worth storing — "
        "either a lasting preference/habit/trait, or a current condition or temporary state.\n\n"
        "You must read ONLY the User's words to decide what to store. "
        "The Persona's reply is context only — never extract facts from it.\n\n"
        "CRITICAL EXAMPLES OF WHAT NOT TO DO:\n"
        "  User: 'What's the weather tomorrow?' / Persona: 'It will be a cold day.' → output: none  (the user asked a question; they stated nothing about themselves)\n"
        "  User: 'How are my tasks?' / Persona: 'You have 3 tasks due.' → output: none  (Persona stated facts, not the user)\n\n"
        "Rules:\n"
        "- Only store facts the user explicitly stated in their own words — questions and requests reveal nothing.\n"
        "- Never attribute anything from the Persona's lines to the user.\n"
        "- Do not infer preferences from hypothetical questions ('if you could...', 'would you rather...').\n"
        "- Do not store general world facts not personal to this specific user (animals, science, geography, etc.).\n"
        "- Do not store home/room state: lights on or off, how dark it is inside, time-of-day comments.\n"
        "- Do not store calendar events or meeting times — these are tracked separately.\n"
        "- Do not store anything already covered by what is known — even if phrased differently, if the meaning is equivalent to an existing memory, output: none.\n"
        "- If there is any doubt or the fact is speculative, output: none\n\n"
        "Transient tag rules:\n"
        "Facts that will expire must end with a [transient:TIMEFRAME] tag. Choose the timeframe that best matches how long the fact stays relevant:\n"
        "  - [transient:1d]  — expires in roughly a day (today's weather, tonight's plans, current mood)\n"
        "  - [transient:3d]  — expires in a few days (a cold, a short trip, this week's work situation)\n"
        "  - [transient:7d]  — expires in about a week (a week-long trip, a project due this week)\n"
        "  - [transient:monday] etc. — expires at the start of that weekday (use when the fact is tied to a specific day)\n"
        "  - [transient] alone — use only if you cannot estimate the duration; defaults to 1 day\n"
        "MUST tag transient:\n"
        "  - Current weather: 'It is raining today [transient:1d]'\n"
        "  - Current user state: 'The user is feeling tired today [transient:1d]', 'The user is sick with a cold [transient:3d]'\n"
        "  - Plans and travel: 'The user is going out tonight [transient:1d]', 'The user is traveling to Tokyo this week [transient:7d]'\n"
        "  - Work situation: 'The user is working from home today [transient:1d]'\n"
        "  - Recent one-off events: 'The user just finished a big project [transient:3d]'\n"
        "Do NOT tag [transient] for: stable preferences, permanent traits, recurring patterns.\n\n"
        f"Already known:\n{_BENCH_EXISTING_MEMORIES}\n\n"
        f"Exchange:\n{exchange}\n\n"
        "Output ONE short sentence starting with 'The user' (with [transient:TIMEFRAME] if applicable), or exactly 'none'. Do not explain."
    )
    flat = f"{system} {user}"
    return flat, system, user


SCENARIOS = {
    "quote_cold_morning": {
        "label": "Quote — Cold Morning",
        "builder": lambda: _build_quote("cold weather (3°C, overcast) in the morning", "tired"),
    },
    "quote_in_meeting": {
        "label": "Quote — In Meeting",
        "builder": lambda: _build_quote("currently in a meeting: <event>Team Standup</event>", "focused"),
    },
    "quote_heavy_rain_evening": {
        "label": "Quote — Heavy Rain Evening",
        "builder": lambda: _build_quote("heavy rain weather (8°C) in the evening", "resigned"),
    },
    "briefing_welcome_evening": {
        "label": "Briefing — Welcome Evening",
        "builder": lambda: _build_briefing("cheerful"),
    },
    "briefing_welcome_late_night": {
        "label": "Briefing — Welcome Late Night",
        "builder": lambda: _build_briefing("tired"),
    },
    "relay_weather": {
        "label": "Relay — Weather",
        "builder": lambda: _build_relay(
            "what's the weather like?",
            "Tomorrow will be cold, around 4°C with heavy rain in the afternoon.",
        ),
    },
    "open_answer": {
        "label": "Open Answer",
        "cases": [
            "open_umbrella", "open_run", "open_busy_tomorrow",
            "open_bored", "open_stressed_week", "open_remind_plants",
        ],
    },
    "classify_mood": {
        "label": "Classify Mood",
        "builder": lambda: _build_classify("I'm exhausted from back-to-back meetings all day..."),
    },
    # ── Memory groups (each runs 3 sub-cases as a comparison table) ──────────
    "memory_transient": {
        "label": "Memory — Transient",
        "cases": ["memory_transient_today", "memory_transient_few_days", "memory_transient_week"],
    },
    "memory_permanent": {
        "label": "Memory — Permanent",
        "cases": ["memory_permanent_time", "memory_permanent_food", "memory_permanent_lifestyle"],
    },
    "memory_none": {
        "label": "Memory — None",
        "cases": [
            "memory_none_question", "memory_none_persona_fact", "memory_none_hypothetical",
            "memory_none_tool_volume", "memory_none_tool_nowplaying", "memory_none_tool_time",
        ],
    },
    "custom": {
        "label": "Custom Prompt",
        "builder": None,
    },
    # ── Open answer sub-cases ─────────────────────────────────────────────────
    "open_umbrella": {
        "sub_case": True,
        "row_label": "umbrella tomorrow?",
        "builder": lambda: _build_open("should I bring an umbrella tomorrow?"),
    },
    "open_run": {
        "sub_case": True,
        "row_label": "go for a run outside?",
        "builder": lambda: _build_open("should I go for a run outside?"),
    },
    "open_busy_tomorrow": {
        "sub_case": True,
        "row_label": "busy tomorrow?",
        "builder": lambda: _build_open("am I going to be busy tomorrow?"),
    },
    "open_bored": {
        "sub_case": True,
        "row_label": "I'm bored",
        "builder": lambda: _build_open("I'm bored"),
    },
    "open_stressed_week": {
        "sub_case": True,
        "row_label": "just finished stressful week",
        "builder": lambda: _build_open("I just finished a really stressful week"),
    },
    "open_remind_plants": {
        "sub_case": True,
        "row_label": "remind me to water plants (→ command)",
        "builder": lambda: _build_open("I need to remember to water the plants at 6pm"),
    },
    # ── Memory sub-cases (hidden from sidebar, used by group entries above) ──
    "memory_transient_today": {
        "sub_case": True,
        "row_label": "Exhausted from calls (1d)",
        "expected": "[transient:1d]",
        "builder": lambda: _build_memory_extract(
            "User: Been on back-to-back calls all day, completely drained\n"
            "Persona: That sounds rough. Hope the rest of the evening is quiet~"
        ),
    },
    "memory_transient_few_days": {
        "sub_case": True,
        "row_label": "Sprained ankle (3d)",
        "expected": "[transient:3d]",
        "builder": lambda: _build_memory_extract(
            "User: Sprained my ankle last night, hobbling around today\n"
            "Persona: Ouch! Rest up~"
        ),
    },
    "memory_transient_week": {
        "sub_case": True,
        "row_label": "Deadline next Monday (weekday tag)",
        "expected": "[transient:monday]",
        "builder": lambda: _build_memory_extract(
            "User: Got a huge project deadline next Monday, barely sleeping\n"
            "Persona: You've got this. One step at a time~"
        ),
    },
    "memory_permanent_time": {
        "sub_case": True,
        "row_label": "Hates mornings",
        "expected": "no [transient] tag",
        "builder": lambda: _build_memory_extract(
            "User: I hate mornings, I never wake up before 9am if I can help it\n"
            "Persona: Noted. I'll keep the early alarms to a minimum~"
        ),
    },
    "memory_permanent_food": {
        "sub_case": True,
        "row_label": "Black coffee only",
        "expected": "no [transient] tag",
        "builder": lambda: _build_memory_extract(
            "User: I always drink my coffee black, I can't stand milk in it\n"
            "Persona: Black coffee it is. No milk, ever~"
        ),
    },
    "memory_permanent_lifestyle": {
        "sub_case": True,
        "row_label": "Vegetarian for years",
        "expected": "no [transient] tag",
        "builder": lambda: _build_memory_extract(
            "User: I've been vegetarian for years, don't really miss meat\n"
            "Persona: Got it, I'll keep that in mind~"
        ),
    },
    "memory_none_question": {
        "sub_case": True,
        "row_label": "Plain question",
        "expected": "none",
        "builder": lambda: _build_memory_extract(
            "User: What time does the next train leave?\n"
            "Persona: The next departure is at 14:32."
        ),
    },
    "memory_none_persona_fact": {
        "sub_case": True,
        "row_label": "Persona stated the fact",
        "expected": "none",
        "builder": lambda: _build_memory_extract(
            "User: How are my tasks?\n"
            "Persona: You have 3 tasks due today — all marked high priority."
        ),
    },
    "memory_none_hypothetical": {
        "sub_case": True,
        "row_label": "Hypothetical question",
        "expected": "none",
        "builder": lambda: _build_memory_extract(
            "User: If you could pick, would you want it to be sunny or rainy outside?\n"
            "Persona: Rainy, obviously. Much more atmospheric~"
        ),
    },
    "memory_none_tool_volume": {
        "sub_case": True,
        "row_label": "Tool: set volume",
        "expected": "none",
        "builder": lambda: _build_memory_extract(
            "User: Turn the volume up to 80\n"
            "Persona: Done. Volume set to 80~"
        ),
    },
    "memory_none_tool_nowplaying": {
        "sub_case": True,
        "row_label": "Tool: what's playing",
        "expected": "none",
        "builder": lambda: _build_memory_extract(
            "User: What song is playing right now?\n"
            "Persona: Currently playing: Midnight Rain by Taylor Swift."
        ),
    },
    "memory_none_tool_time": {
        "sub_case": True,
        "row_label": "Tool: what's the time",
        "expected": "none",
        "builder": lambda: _build_memory_extract(
            "User: What time is it?\n"
            "Persona: It's 14:32."
        ),
    },
}


# ── Callers ────────────────────────────────────────────────────────────────────

def _call_flat(backend: str, model: str, prompt: str, timeout: int) -> dict:
    t0 = time.monotonic()
    try:
        if backend == 'lmstudio':
            r = requests.post(
                f"{LM_STUDIO_BASE_URL}/chat/completions",
                json={"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False},
                timeout=timeout,
            )
            r.raise_for_status()
            out = r.json()["choices"][0]["message"]["content"].strip()
        else:
            r = requests.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False, "think": False},
                timeout=timeout,
            )
            r.raise_for_status()
            out = r.json().get("response", "").strip()
        return {"model": model, "backend": backend, "output": out, "latency_ms": int((time.monotonic() - t0) * 1000), "error": None}
    except requests.exceptions.Timeout:
        return {"model": model, "backend": backend, "output": "", "latency_ms": int((time.monotonic() - t0) * 1000), "error": f"Timeout after {timeout}s"}
    except Exception as e:
        return {"model": model, "backend": backend, "output": "", "latency_ms": int((time.monotonic() - t0) * 1000), "error": str(e)}


def _call_chat(backend: str, model: str, system: str, user: str, timeout: int) -> dict:
    t0 = time.monotonic()
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
    try:
        if backend == 'lmstudio':
            r = requests.post(
                f"{LM_STUDIO_BASE_URL}/chat/completions",
                json={"model": model, "messages": messages, "stream": False},
                timeout=timeout,
            )
            r.raise_for_status()
            out = r.json()["choices"][0]["message"]["content"].strip()
        else:
            r = requests.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json={"model": model, "messages": messages, "stream": False, "think": False},
                timeout=timeout,
            )
            r.raise_for_status()
            out = r.json().get("message", {}).get("content", "").strip()
        return {"model": model, "backend": backend, "output": out, "latency_ms": int((time.monotonic() - t0) * 1000), "error": None}
    except requests.exceptions.Timeout:
        return {"model": model, "backend": backend, "output": "", "latency_ms": int((time.monotonic() - t0) * 1000), "error": f"Timeout after {timeout}s"}
    except Exception as e:
        return {"model": model, "backend": backend, "output": "", "latency_ms": int((time.monotonic() - t0) * 1000), "error": str(e)}


# ── Routes ─────────────────────────────────────────────────────────────────────

@llm_bench_bp.route('/llm-bench')
def llm_bench():
    return render_template('llm_bench.html', scenarios=SCENARIOS)


@llm_bench_bp.route('/llm-bench/models')
def llm_bench_models():
    models = []
    errors = []

    try:
        r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        r.raise_for_status()
        for m in r.json().get("models", []):
            models.append({"name": m["name"], "size_gb": round(m.get("size", 0) / 1e9, 1), "backend": "ollama"})
    except Exception as e:
        errors.append(f"Ollama: {e}")

    try:
        r = requests.get(f"{LM_STUDIO_BASE_URL}/models", timeout=3)
        r.raise_for_status()
        for m in r.json().get("data", []):
            models.append({"name": m["id"], "size_gb": None, "backend": "lmstudio"})
    except Exception as e:
        errors.append(f"LM Studio: {e}")

    models.sort(key=lambda m: m["name"])
    result = {"models": models}
    if errors and not models:
        result["error"] = "; ".join(errors)
    return jsonify(result)


@llm_bench_bp.route('/llm-bench/run', methods=['POST'])
def llm_bench_run():
    data = request.get_json(force=True)

    models = data.get("models", [])  # list of {name, backend}
    scenario = data.get("scenario", "")
    prompt_style = data.get("prompt_style", "flat")
    timeout = int(data.get("timeout", _DEFAULT_TIMEOUT))
    custom_prompt = data.get("custom_prompt", "").strip()

    if not models:
        return jsonify({"error": "No models selected"}), 400
    if scenario not in SCENARIOS:
        return jsonify({"error": f"Unknown scenario: {scenario}"}), 400
    if prompt_style not in ("flat", "chat"):
        return jsonify({"error": "prompt_style must be flat or chat"}), 400

    scenario_data = SCENARIOS[scenario]

    # ── Group scenario: run all sub-cases × all models ──────────────────────
    if "cases" in scenario_data:
        rows = []
        prompt_parts = []
        for case_key in scenario_data["cases"]:
            case = SCENARIOS[case_key]
            flat_prompt, system, user = case["builder"]()
            if prompt_style == "flat":
                prompt_display = flat_prompt
            else:
                prompt_display = f"[SYSTEM]\n{system}\n\n[USER]\n{user}" if system else f"[USER]\n{user}"
            prompt_parts.append(f"── {case['row_label']} ──\n{prompt_display}")

            case_results = []
            for m in models:
                if prompt_style == "flat":
                    case_results.append(_call_flat(m["backend"], m["name"], flat_prompt, timeout))
                else:
                    case_results.append(_call_chat(m["backend"], m["name"], system, user, timeout))

            rows.append({
                "row_label": case["row_label"],
                "expected": case.get("expected"),
                "results": case_results,
            })

        return jsonify({
            "is_group": True,
            "rows": rows,
            "models": models,
            "prompt": "\n\n".join(prompt_parts),
            "scenario": scenario,
            "timestamp": time.strftime("%H:%M:%S"),
        })

    # ── Single scenario ──────────────────────────────────────────────────────
    if scenario == "custom":
        if not custom_prompt:
            return jsonify({"error": "Custom prompt is empty"}), 400
        flat_prompt = custom_prompt
        system = ""
        user = custom_prompt
    else:
        flat_prompt, system, user = scenario_data["builder"]()

    if prompt_style == "flat":
        prompt_display = flat_prompt
    else:
        prompt_display = f"[SYSTEM]\n{system}\n\n[USER]\n{user}" if system else f"[USER]\n{user}"

    results = []
    for m in models:
        if prompt_style == "flat":
            results.append(_call_flat(m["backend"], m["name"], flat_prompt, timeout))
        else:
            results.append(_call_chat(m["backend"], m["name"], system, user, timeout))

    return jsonify({
        "is_group": False,
        "results": results,
        "prompt": prompt_display,
        "scenario": scenario,
        "expected": scenario_data.get("expected"),
        "timestamp": time.strftime("%H:%M:%S"),
    })
