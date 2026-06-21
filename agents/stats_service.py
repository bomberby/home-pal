"""Persona gamification stats — XP, level, affection, energy, streak.

All mutating functions are best-effort: they log and swallow exceptions
so they never break the host operation (chat, task completion, etc.).
"""

import json
import math
import re
import threading
from datetime import datetime, timedelta, date
from pathlib import Path

_CUSTOM_MOODS_FILE = Path('env/custom_moods.json')

# Rolling buffer of recent XP event reasons (last 20); used for level-up mood generation
_recent_events: list[str] = []
_MAX_RECENT_EVENTS = 20

AFFECTION_DECAY_RATE = 3  # points lost per day of inactivity

DEFAULT_UNLOCKED = [
    'cheerful', 'content', 'dreamy', 'tired', 'resigned',
    'flustered', 'focused', 'worried', 'annoyed', 'melancholy',
]

# mood_key → condition(level, affection, streak_days) → bool
_UNLOCK_THRESHOLDS = [
    ('excited',    lambda lvl, aff, s: lvl >= 5),
    ('furious',    lambda lvl, aff, s: lvl >= 8),
    ('lovestruck', lambda lvl, aff, s: lvl >= 10 and aff >= 50),
    ('smug',       lambda lvl, aff, s: lvl >= 15 and s >= 7),
]

# In-memory queue of newly-unlocked moods; popped once by _make_response per cycle
_pending_unlocks: list[str] = []


def _load_custom_moods() -> list[dict]:
    try:
        return json.loads(_CUSTOM_MOODS_FILE.read_text())
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f'[Stats] _load_custom_moods error: {e}')
        return []


def _save_custom_moods(moods: list[dict]) -> None:
    try:
        _CUSTOM_MOODS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CUSTOM_MOODS_FILE.write_text(json.dumps(moods, indent=2))
    except Exception as e:
        print(f'[Stats] _save_custom_moods error: {e}')


def get_custom_mood_modifier(key: str) -> str | None:
    """Return the SD modifier for a custom LLM-generated mood key, or None."""
    for m in _load_custom_moods():
        if m.get('key') == key:
            return m.get('modifier')
    return None


def _generate_level_up_mood(new_level: int, events: list[str]) -> None:
    """Background thread: ask Ollama to invent a new mood; store it and surface as unlock."""
    try:
        from agents.llm.ollama_service import call_ollama
        from agents.memory_service import MemoryService

        existing_custom = [m['key'] for m in _load_custom_moods()]
        memories = MemoryService.load()[-5:]
        memory_text = '; '.join(m['content'] for m in memories) if memories else 'none yet'
        event_text = ', '.join(events[-15:]) if events else 'none'

        base_moods = [
            'cheerful', 'content', 'dreamy', 'tired', 'resigned', 'flustered',
            'focused', 'worried', 'excited', 'furious', 'annoyed', 'melancholy',
            'smug', 'lovestruck',
        ] + existing_custom

        from agents.persona.states import CHARACTER_VOICE, CHARACTER_PREFIX
        system = (
            "You are designing a new emotion for Ren, a home dashboard anime character.\n\n"
            "CHARACTER VOICE (who Ren is):\n"
            f"{CHARACTER_VOICE}\n\n"
            "CHARACTER APPEARANCE (SD anchor — her physical style):\n"
            f"{CHARACTER_PREFIX}\n\n"
            "Your task: invent ONE new emotional expression that fits Ren's personality and the context below.\n\n"
            "Key (emotion name) rules:\n"
            "- Unique snake_case word (not in the forbidden list)\n"
            "- Must feel like something Ren would actually feel — she is sharp, quietly dramatic, privately caring, a little possessive\n"
            "- Draw from the context: what she has been doing, what she knows about the user\n\n"
            "SD modifier — FORMAT IS CRITICAL:\n"
            "The modifier is a Stable Diffusion prompt fragment: comma-separated visual descriptor tokens.\n"
            "It is NOT a sentence. It has NO verbs, NO pronouns, NO character names, NO possessives.\n"
            "Each token is a short noun phrase or adjective describing something visually present in the image.\n\n"
            "Existing modifiers (match this exact style):\n"
            "  tired:     (half-closed heavy eyelids:1.3), head drooping forward, shoulders slumped, mouth slightly open\n"
            "  flustered: (flushed red cheeks:1.2), wide startled eyes, mouth slightly open\n"
            "  smug:      confident smirk, one eyebrow raised, self-satisfied composed posture\n"
            "  melancholy:(downcast eyes:1.2), faint trembling lip, withdrawn hunched posture\n\n"
            "Rules:\n"
            "- Comma-separated tokens only — no full sentences with subject + conjugated verb\n"
            "- Participles and gerunds are fine: 'smiling', 'trembling', 'leaning forward', 'crying'\n"
            "- No second-person references (yours, your, you), no character name (Ren), no possessives\n"
            "- At most one weighted phrase (phrase:1.1–1.5); not every token needs a weight\n"
            "- May include expression, posture, gesture, outfit detail, or environment\n"
            "- Do NOT repeat CHARACTER_PREFIX elements: no 'dark navy hair', no 'amber eyes', no 'soft features'\n"
            "- Do NOT use contradictory descriptors for the same body part\n\n"
            "WRONG: \"Ren's amber eyes narrowed as her hand tightened around yours\"\n"
            "RIGHT: \"(narrowed eyes:1.2), hand gripping firmly, composed upright posture\"\n\n"
            f"Forbidden emotion keys (already exist): {', '.join(base_moods)}\n\n"
            "OUTPUT FORMAT — strictly enforced:\n"
            'The JSON must have exactly two fields named "key" and "modifier" — no other field names.\n'
            "Your entire response must be exactly one JSON object. Start with { and end with }.\n"
            "No text before it, no text after it, no code fences, no explanation.\n"
            "The modifier string must be UNDER 120 CHARACTERS. Stop when you reach that limit.\n"
            'Correct:   {"key": "wistful", "modifier": "(soft faraway gaze:1.2), slight pout, shoulders turned inward"}\n'
            'Incorrect: {"emotion": "wistful", ...}   ← wrong field name, must be "key"\n'
            'Incorrect: {"key": "wistful", "modifier": "feeling of X, feeling of Y, feeling of Z ..."}   ← repetitive filler'
        )
        user = (
            f"Character reached level {new_level}.\n"
            f"Recent XP events: {event_text}\n"
            f"What we know about the user: {memory_text}"
        )

        # Two-phase: bounded thinking then fast think=False answer.
        # Single think=True call starves on tokens — model finishes thinking with nothing
        # left for the answer, call_lmstudio returns None with no useful log.
        from agents.llm.ollama_service import collect_thinking
        print(f'[Stats] level-up mood: starting think phase (budget=6000, timeout=120s)')
        thinking = collect_thinking(user, think_budget_chars=6000, timeout=120, system=system)
        print(f'[Stats] level-up mood: think phase done — {len(thinking) if thinking else 0} chars')

        _json_format = (
            "Output exactly one JSON object. Start with { and end with }. "
            "No code fences, no explanation, no text outside the braces."
        )
        if thinking:
            phase2_system = f"You have already reasoned through this task. {_json_format}"
            phase2_user = (
                f"<thinking>\n{thinking}\n</thinking>\n\n"
                f"{user}\n\n"
                'Write the JSON now. Begin your response with {"key":'
            )
        else:
            print('[Stats] level-up mood: no thinking collected, falling back to direct call')
            phase2_system = system + f"\n\n{_json_format}"
            phase2_user = user + '\n\nWrite the JSON now. Begin your response with {"key":'

        print(f'[Stats] level-up mood: starting answer phase (think=False, timeout=45s)')
        raw = call_ollama(phase2_user, timeout=45, system=phase2_system, think=False)
        print(f'[Stats] level-up mood: answer phase returned {len(raw) if raw else 0} chars: {raw!r}')
        if not raw:
            print('[Stats] level-up mood: LLM returned nothing — check LMStudio/Ollama logs above')
            return

        # Model was primed with '{"key":' so the raw response is the completion
        if not raw.lstrip().startswith('{'):
            raw = '{"key":' + raw

        # Extract outermost { } — robust against trailing prose or stray characters
        start = raw.find('{')
        end = raw.rfind('}')
        if start == -1 or end == -1 or end <= start:
            print(f'[Stats] level-up mood: no JSON delimiters found — raw: {raw!r}')
            return
        candidate = raw[start:end + 1]
        print(f'[Stats] level-up mood: parsing candidate: {candidate!r}')
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as exc:
            print(f'[Stats] level-up mood: JSON parse error at pos {exc.pos}: {exc.msg}')
            print(f'[Stats] level-up mood: around error: {candidate[max(0, exc.pos-30):exc.pos+30]!r}')
            return

        key = data.get('key', '').strip().lower().replace(' ', '_')
        modifier = data.get('modifier', '').strip()
        # Hard-truncate at 120 chars in case model ignores the length instruction
        if len(modifier) > 120:
            modifier = modifier[:120].rsplit(',', 1)[0].strip()
            print(f'[Stats] level-up mood: modifier truncated to {len(modifier)} chars')

        if not re.fullmatch(r'[a-z][a-z0-9_]{1,30}', key):
            print(f'[Stats] level-up mood: invalid key {key!r}')
            return
        if len(modifier) < 10:
            print(f'[Stats] level-up mood: modifier too short: {modifier!r}')
            return
        if key in base_moods:
            print(f'[Stats] level-up mood: key {key!r} already exists, skipping')
            return

        entry = {
            'key': key,
            'modifier': modifier,
            'level': new_level,
            'created_at': datetime.now().isoformat(timespec='seconds'),
        }
        custom = _load_custom_moods()
        custom.append(entry)
        _save_custom_moods(custom)
        print(f'[Stats] New level-up mood created: {key!r} → "{modifier}"')

        # Add to PersonaStats.unlocked_moods and surface the toast
        from models import PersonaStats
        row = PersonaStats.singleton()
        moods = json.loads(row.unlocked_moods) if row.unlocked_moods else list(DEFAULT_UNLOCKED)
        if key not in moods:
            moods.append(key)
            row.unlocked_moods = json.dumps(moods)
            row.save()
        _pending_unlocks.append(key)

    except Exception as e:
        print(f'[Stats] _generate_level_up_mood error: {e}')


def _affection_floor(level: int) -> int:
    """Permanent minimum affection for this level. Rises with level, caps at 60."""
    return min(60, level * 3)


def _effective_affection(affection: int, level: int, last_event: datetime | None) -> int:
    """Affection after applying time-based decay since the last XP event."""
    if last_event is None:
        return affection
    days = max(0.0, (datetime.now() - last_event).total_seconds() / 86400)
    decay = int(days * AFFECTION_DECAY_RATE)
    return max(_affection_floor(level), affection - decay)


def _apply_affection_decay(row) -> None:
    """Mutate row.affection in-place to its effective value. Caller must save()."""
    level = _level_from_xp(row.xp)
    row.affection = _effective_affection(row.affection, level, row.last_xp_event_at)


def _level_from_xp(xp: int) -> int:
    return 1 + int(math.floor(math.sqrt(max(0, xp) / 50)))


def _xp_bracket(level: int) -> tuple[int, int]:
    """Return (xp_at_level_start, xp_at_next_level_start)."""
    return 50 * (level - 1) ** 2, 50 * level ** 2


def energy_now(affection: int = 0) -> int:
    """Energy 20–100: decays 4 pts/hour from 5am; affection adds a small floor boost."""
    now = datetime.now()
    today_5am = now.replace(hour=5, minute=0, second=0, microsecond=0)
    if now < today_5am:
        today_5am -= timedelta(days=1)
    hours = (now - today_5am).total_seconds() / 3600
    base = max(20, round(100 - 4 * hours))
    boost = min(20, affection // 5)
    return min(100, base + boost)


def is_enabled() -> bool:
    from models import PersonaStats
    try:
        return bool(PersonaStats.singleton().enabled)
    except Exception:
        return True  # fail open


def toggle_enabled() -> bool:
    """Flip the enabled flag; return the new value."""
    from models import PersonaStats
    try:
        row = PersonaStats.singleton()
        row.enabled = not row.enabled
        row.save()
        state = 'enabled' if row.enabled else 'disabled'
        print(f'[Stats] gamification {state}')
        return row.enabled
    except Exception as e:
        print(f'[Stats] toggle_enabled error: {e}')
        return True


def get() -> dict:
    from models import PersonaStats
    try:
        row = PersonaStats.singleton()
        moods = json.loads(row.unlocked_moods) if row.unlocked_moods else list(DEFAULT_UNLOCKED)
        lvl = _level_from_xp(row.xp)
        lvl_start, lvl_next = _xp_bracket(lvl)
        eff_aff = _effective_affection(row.affection, lvl, row.last_xp_event_at)
        return {
            'level': lvl,
            'xp': row.xp,
            'xp_progress': row.xp - lvl_start,
            'xp_needed': lvl_next - lvl_start,
            'affection': eff_aff,
            'affection_floor': _affection_floor(lvl),
            'energy': energy_now(eff_aff),
            'streak_days': row.streak_days,
            'unlocked_moods': moods,
            'custom_moods': _load_custom_moods(),
            'enabled': bool(row.enabled),
        }
    except Exception as e:
        print(f'[Stats] get() error: {e}')
        return {}


def add_xp(amount: int, reason: str = '') -> None:
    from models import PersonaStats
    try:
        row = PersonaStats.singleton()
        old_level = _level_from_xp(row.xp)
        _apply_affection_decay(row)  # reconcile before updating last_xp_event_at
        row.xp += amount
        row.last_xp_event_at = datetime.now()
        row.save()
        new_level = _level_from_xp(row.xp)
        if reason:
            _recent_events.append(reason)
            if len(_recent_events) > _MAX_RECENT_EVENTS:
                _recent_events.pop(0)
        print(f'[Stats] +{amount} XP ({reason}) → total {row.xp} Lv{new_level}')
        if new_level > old_level:
            print(f'[Stats] Level up! {old_level} → {new_level}')
            threading.Thread(
                target=_generate_level_up_mood,
                args=(new_level, list(_recent_events)),
                daemon=True,
            ).start()
        _check_unlocks()
    except Exception as e:
        print(f'[Stats] add_xp error: {e}')


def add_affection(amount: int) -> None:
    from models import PersonaStats
    try:
        row = PersonaStats.singleton()
        _apply_affection_decay(row)  # apply pending decay before adding
        row.affection = min(100, row.affection + amount)
        row.save()
        _check_unlocks()
    except Exception as e:
        print(f'[Stats] add_affection error: {e}')


def tick_streak() -> None:
    """Advance the daily streak counter. Also grants the day's streak XP bonus (once per day)."""
    from models import PersonaStats
    try:
        row = PersonaStats.singleton()
        today = date.today()
        last = row.last_seen_date
        if last is None:
            row.streak_days = 1
            new_day = True
        elif last == today:
            return  # already ticked today, nothing to do
        elif (today - last).days == 1:
            row.streak_days += 1
            new_day = True
        else:
            row.streak_days = 1  # gap > 1 day — streak resets
            new_day = True
        row.last_seen_date = today
        row.save()
        print(f'[Stats] Streak tick → {row.streak_days} day(s)')
        if new_day:
            bonus = 10 * min(row.streak_days, 7)
            add_xp(bonus, 'daily_streak')
        _check_unlocks()
    except Exception as e:
        print(f'[Stats] tick_streak error: {e}')


def unlocked_moods_set(stats: dict | None = None) -> set[str]:
    if stats is None:
        stats = get()
    return set(stats.get('unlocked_moods', DEFAULT_UNLOCKED))


def pop_pending_unlock() -> str | None:
    """Return and remove the first pending unlock notification, or None."""
    if not is_enabled():
        return None
    return _pending_unlocks.pop(0) if _pending_unlocks else None


def apply_mood_overlay(mood: str, period: str | None, stats: dict, base_key: str) -> str:
    """Nudge mood based on stats — only when base mood is the bland 'content' default."""
    if not stats.get('enabled', True):
        return mood
    if mood != 'content':
        return mood
    unlocked = unlocked_moods_set(stats)
    nrg = stats.get('energy', 100)
    aff = stats.get('affection', 0)
    streak = stats.get('streak_days', 0)
    if nrg < 30 and 'tired' in unlocked:
        return 'tired'
    if aff >= 80 and period in ('morning', 'evening') and 'cheerful' in unlocked:
        return 'cheerful'
    if streak >= 5 and base_key.startswith('welcome'):
        if 'smug' in unlocked:
            return 'smug'
        if 'cheerful' in unlocked:
            return 'cheerful'
    # Surface a recently-created custom mood for 24h after its level-up generation
    cutoff = datetime.now() - timedelta(hours=24)
    for entry in reversed(_load_custom_moods()):
        key = entry.get('key', '')
        try:
            created = datetime.fromisoformat(entry.get('created_at', ''))
        except ValueError:
            continue
        if created >= cutoff and key in unlocked:
            return key
    return mood


# ------------------------------------------------------------------ #
#  Event hooks — one call per trigger site, logic lives here          #
# ------------------------------------------------------------------ #

def on_chat(factual: bool = False, mood: str | None = None) -> None:
    if not is_enabled(): return
    add_xp(4 if not factual else 2, 'chat')
    if mood in ('lovestruck', 'cheerful'):
        add_affection(1)
    tick_streak()


def on_task_done() -> None:
    if not is_enabled(): return
    add_xp(10, 'task_done')


def on_reminder_fired() -> None:
    if not is_enabled(): return
    add_xp(5, 'reminder')


def on_wish_answered() -> None:
    if not is_enabled(): return
    add_xp(20, 'wish')
    add_affection(5)


def on_memory_stored(source: str = '') -> None:
    if not is_enabled(): return
    if source == 'system':
        return  # avoid self-feeding from observe()
    add_xp(5, 'memory')
    add_affection(2)


def on_welcome() -> None:
    if not is_enabled(): return
    add_xp(15, 'welcome')
    add_affection(3)
    tick_streak()


def _check_unlocks() -> None:
    from models import PersonaStats
    try:
        row = PersonaStats.singleton()
        moods = json.loads(row.unlocked_moods) if row.unlocked_moods else list(DEFAULT_UNLOCKED)
        lvl = _level_from_xp(row.xp)
        changed = False
        for mood_key, condition in _UNLOCK_THRESHOLDS:
            if mood_key not in moods and condition(lvl, row.affection, row.streak_days):
                moods.append(mood_key)
                _pending_unlocks.append(mood_key)
                changed = True
                print(f'[Stats] Unlocked mood: {mood_key}!')
        if changed:
            row.unlocked_moods = json.dumps(moods)
            row.save()
    except Exception as e:
        print(f'[Stats] _check_unlocks error: {e}')
