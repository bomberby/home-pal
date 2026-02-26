import re
import threading
import time


class TimerService:
    _timers: dict[str, dict] = {}
    _counter = 0
    _lock = threading.Lock()

    @classmethod
    def start_timer(cls, seconds: int, label: str = "Timer") -> str:
        with cls._lock:
            cls._counter += 1
            timer_id = str(cls._counter)

        expires_at = time.time() + seconds
        thread = threading.Thread(
            target=cls._run,
            args=(timer_id, label, seconds),
            daemon=True,
        )
        with cls._lock:
            cls._timers[timer_id] = {
                'label': label,
                'expires': expires_at,
                'thread': thread,
            }
        thread.start()
        return f"Timer set for {cls._format_duration(seconds)}."

    @classmethod
    def _run(cls, timer_id: str, label: str, seconds: int):
        time.sleep(seconds)
        with cls._lock:
            cls._timers.pop(timer_id, None)
        print(f"[Timer] '{label}' done!")
        try:
            from services.telegram_service import TelegramService
            from agents.persona_agent import PersonaAgent
            situation = f"a countdown timer just finished: '{label}'"
            message = PersonaAgent.generate_reactive_line(situation)
            TelegramService.send_message(message, photo=TelegramService.get_image_for_text(message))
        except Exception as e:
            print(f"[Timer] Telegram notification failed: {e}")

    @classmethod
    def list_timers(cls) -> str:
        with cls._lock:
            active = {tid: t for tid, t in cls._timers.items() if t['thread'].is_alive()}
        if not active:
            return "No active timers."
        parts = []
        for t in active.values():
            remaining = max(0, int(t['expires'] - time.time()))
            parts.append(f"'{t['label']}': {cls._format_duration(remaining)} left")
        return "Active timers: " + ", ".join(parts) + "."

    # --- Natural language helpers ---

    @staticmethod
    def parse_duration(text: str) -> int | None:
        """Parse a duration in seconds from natural language.
        Handles: '10 minutes', '1 hour 30 min', '45 seconds', '2h 15m', etc.
        """
        total = 0
        found = False
        for match in re.finditer(
            r'(\d+)\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b',
            text,
            re.IGNORECASE,
        ):
            num = int(match.group(1))
            unit = match.group(2).lower()
            if unit.startswith('h'):
                total += num * 3600
            elif unit.startswith('m'):
                total += num * 60
            else:
                total += num
            found = True
        return total if found else None

    @staticmethod
    def extract_label(query: str) -> str:
        """Pull a human-friendly label from a timer query if one was given.
        e.g. 'set a timer for 10 minutes for the pasta' → 'the pasta'
        """
        match = re.search(
            r'(?:timer\s+for\s+[\dhms\s]+(?:minutes?|hours?|seconds?))\s+for\s+(.+)',
            query,
            re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()
        return "Timer"

    @staticmethod
    def _format_duration(seconds: int) -> str:
        if seconds < 60:
            return f"{seconds} second{'s' if seconds != 1 else ''}"
        minutes = seconds // 60
        remaining_secs = seconds % 60
        if minutes < 60:
            s = f"{minutes} minute{'s' if minutes != 1 else ''}"
            if remaining_secs:
                s += f" {remaining_secs}s"
            return s
        hours = minutes // 60
        mins = minutes % 60
        s = f"{hours} hour{'s' if hours != 1 else ''}"
        if mins:
            s += f" {mins} minute{'s' if mins != 1 else ''}"
        return s
