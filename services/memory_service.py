import json
import os
import re
from datetime import datetime, timedelta


class MemoryService:
    MEMORY_PATH = os.path.join('env', 'persona_memory.json')
    MAX_MEMORIES = 20

    # Default TTLs
    _OBSERVE_TTL_HOURS   = 72   # system-observed habits: 3 days before they must re-confirm
    _TRANSIENT_TTL_HOURS = 24   # fallback when [transient] has no timeframe

    _DAY_NAMES = {
        'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
        'friday': 4, 'saturday': 5, 'sunday': 6,
    }

    @classmethod
    def load(cls) -> list[dict]:
        """Load memories, pruning any that have expired."""
        try:
            with open(cls.MEMORY_PATH) as f:
                data = json.load(f)
            memories = data if isinstance(data, list) else []
        except FileNotFoundError:
            return []
        except Exception as e:
            print(f"[Memory] Could not read {cls.MEMORY_PATH}: {e}")
            return []

        now = datetime.now()
        live = [m for m in memories if not cls._is_expired(m, now)]
        if len(live) < len(memories):
            print(f"[Memory] Pruned {len(memories) - len(live)} expired memories.")
            cls._save(live)
        return live

    @classmethod
    def _is_expired(cls, memory: dict, now: datetime) -> bool:
        expires_at = memory.get('expires_at')
        if not expires_at:
            return False
        try:
            return datetime.fromisoformat(expires_at) < now
        except ValueError:
            return False

    @classmethod
    def _save(cls, memories: list[dict]):
        os.makedirs(os.path.dirname(cls.MEMORY_PATH), exist_ok=True)
        with open(cls.MEMORY_PATH, 'w') as f:
            json.dump(memories, f, indent=2)

    @classmethod
    def add(cls, content: str, source: str, ttl_hours: int | None = None):
        memories = cls.load()
        entry = {
            "content":    content,
            "source":     source,
            "timestamp":  datetime.now().isoformat(timespec='seconds'),
            "expires_at": (datetime.now() + timedelta(hours=ttl_hours)).isoformat(timespec='seconds')
                          if ttl_hours is not None else None,
        }
        memories.append(entry)
        if len(memories) > cls.MAX_MEMORIES:
            memories = memories[-cls.MAX_MEMORIES:]
        cls._save(memories)
        ttl_str = f" (expires in {ttl_hours}h)" if ttl_hours else ""
        print(f"[Memory] Stored ({source}){ttl_str}: {content}")

    @classmethod
    def get_all(cls) -> list[dict]:
        return cls.load()

    @classmethod
    def remove_at(cls, index: int):
        """Remove memory at 1-based index. Raises IndexError if out of range."""
        memories = cls.load()
        if index < 1 or index > len(memories):
            raise IndexError(f"No memory at index {index}")
        removed = memories.pop(index - 1)
        cls._save(memories)
        print(f"[Memory] Removed: {removed['content']}")

    @classmethod
    def clear(cls):
        cls._save([])
        print("[Memory] All memories cleared.")

    @classmethod
    def format_for_prompt(cls) -> str:
        memories = cls.load()
        if not memories:
            return ""
        items = " • ".join(m["content"] for m in memories)
        return f"Background awareness (silently inform your tone and personalisation only — never reference these facts, ask about them, suggest alternatives based on them, or comment on what you know about the user): {items}"

    @classmethod
    def has_similar(cls, keyword: str) -> bool:
        """Return True if any existing memory contains keyword (case-insensitive)."""
        return any(keyword.lower() in m["content"].lower() for m in cls.load())

    @classmethod
    def _parse_ttl(cls, timeframe: str) -> int:
        """Convert a [transient:TIMEFRAME] value to TTL in hours.

        Supports:
          - Empty string → default 24h fallback
          - 'today' → 24h
          - 'Nd'    → N days  (e.g. '3d' → 72h)
          - 'Nh'    → N hours (e.g. '6h' → 6h)
          - Weekday → hours until that weekday starts (e.g. 'monday' when today is Wed → 5 days)
        """
        t = timeframe.lower().strip()
        if not t or t == 'today':
            return cls._TRANSIENT_TTL_HOURS
        if t.endswith('d') and t[:-1].isdigit():
            return int(t[:-1]) * 24
        if t.endswith('h') and t[:-1].isdigit():
            return int(t[:-1])
        if t in cls._DAY_NAMES:
            today = datetime.now().weekday()
            target = cls._DAY_NAMES[t]
            days_until = (target - today) % 7 or 7   # 0 → next occurrence (7 days)
            return days_until * 24
        return cls._TRANSIENT_TTL_HOURS  # unknown format → fallback

    @classmethod
    def _parse_llm_memory(cls, result: str) -> tuple[str | None, int | None]:
        """
        Parse a raw LLM memory response.

        Returns (content, ttl_hours) or (None, None) if the LLM decided not to store.
        ttl_hours is None for permanent memories, an integer for transient ones.
        Rules:
          - Take only the first non-empty line to prevent 'fact + doubt' multi-paragraph leakage.
          - If that line is 'none' (case-insensitive), discard.
          - Strip the [transient:TIMEFRAME] tag and compute ttl_hours from the timeframe.
        """
        first_line = next((l.strip() for l in result.splitlines() if l.strip()), "")
        if not first_line or first_line.lower().rstrip('.,!') == 'none':
            return None, None
        match = re.search(r'\[transient(?::([^\]]*))?\]', first_line, re.IGNORECASE)
        if match:
            ttl_hours = cls._parse_ttl(match.group(1) or '')
            content = first_line[:match.start()].rstrip(' .,')
        else:
            ttl_hours = None
            content = first_line
        if len(content) < 15:
            return None, None
        return content, ttl_hours

    @classmethod
    def extract_from_exchange(cls, exchange: str) -> None:
        """Ask Ollama if the exchange reveals a user fact (permanent or transient); store it if so."""
        from services.ollama_service import call_ollama
        existing = cls.load()
        existing_text = (
            "\n".join(f"- {m['content']}" for m in existing)
            if existing else "None yet."
        )
        prompt = (
            "The exchange below has a 'User:' line and a 'Persona:' line. "
            "You must read ONLY the User's words to decide what to store. "
            "The Persona's reply is context only — never extract facts from it.\n\n"
            "CRITICAL EXAMPLES OF WHAT NOT TO DO:\n"
            "  User: 'What's the weather tomorrow?' / Persona: 'It will be a cold day.' → output: none  (the user asked a question; they stated nothing about themselves)\n"
            "  User: 'How are my tasks?' / Persona: 'You have 3 tasks due.' → output: none  (Persona stated facts, not the user)\n\n"
            "Review the User's words only and decide if they reveal a fact worth storing — "
            "either a lasting preference/habit/trait, or a current condition or temporary state.\n\n"
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
            f"Already known:\n{existing_text}\n\n"
            f"Exchange:\n{exchange}\n\n"
            "Output ONE short sentence starting with 'The user' (with [transient:TIMEFRAME] if applicable), or exactly 'none'. Do not explain."
        )
        result = call_ollama(prompt, timeout=15)
        if not result:
            return
        content, ttl_hours = cls._parse_llm_memory(result)
        if content:
            cls.add(content, "user", ttl_hours=ttl_hours)

    @classmethod
    def observe(cls, situation: str) -> None:
        """Ask Ollama if a system-observed event reveals a new habit or pattern worth storing.

        Observed memories expire after OBSERVE_TTL_HOURS — they must re-confirm before
        being treated as established habits. Passes existing memories for semantic dedup.
        """
        from services.ollama_service import call_ollama
        existing = cls.load()
        existing_text = (
            "\n".join(f"- {m['content']}" for m in existing)
            if existing else "None yet."
        )
        prompt = (
            "You are a memory assistant for a home dashboard persona. "
            "Given what is already known about the user and a new system observation, "
            "decide if the observation reveals a new recurring habit or pattern worth remembering long-term.\n\n"
            "Rules:\n"
            "- Only store patterns that have been observed repeatedly or are clearly habitual — a single occurrence is not a habit.\n"
            "- Do not store anything already covered by what is known.\n"
            "- Do not speculate about what an observation might imply — only state what was directly observed as a pattern.\n"
            "- If there is any doubt, output: none\n\n"
            f"Already known:\n{existing_text}\n\n"
            f"New observation: {situation}\n\n"
            "Output ONE short sentence or exactly 'none'. Do not explain your reasoning."
        )
        result = call_ollama(prompt, timeout=15)
        if not result:
            return
        content, _ = cls._parse_llm_memory(result)
        if content:
            cls.add(content, "observed", ttl_hours=cls._OBSERVE_TTL_HOURS)
