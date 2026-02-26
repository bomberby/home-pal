import re
import datetime
from agents.weather_agent_service import WeatherAgentService
from agents.calendar_agent_service import CalendarAgentService
from smart_home.smart_home_service import get_device_status, change_device_status

VOLUME_STEP = 10  # percent to adjust per "louder/quieter" command

HELP_TEXT = (
    "I can: check weather (today or tomorrow), view calendar events (today or tomorrow), "
    "manage your shopping list and to-do list (add or show), "
    "control Spotify (play, pause, skip, previous, what's playing, volume), "
    "set countdown timers, set time-based reminders ('remind me at 18:30 to call the vet'), "
    "and control the lights (on, off, rainbow mode)."
)


class AgentService:
    @classmethod
    def handle_query(cls, query: str) -> str:
        """Process the user query and return a text response."""
        q = query.lower().strip()
        words = set(q.split())

        # --- Help ---
        if words.intersection({"help", "commands", "capabilities"}) or (
            "what" in q and "can" in q and "you" in q
        ):
            return HELP_TEXT

        # --- Reminders (before timer — both share "remind"/"time" proximity) ---
        if "remind" in q:
            return cls._handle_reminder(query, q)

        # --- Timers (before time check — "timer" contains "time") ---
        if "timer" in q:
            return cls._handle_timer(query, q)

        # --- Time / date ---
        if "time" in q:
            return cls._get_time()
        if "date" in q:
            return cls._get_date()

        # --- Weather ---
        if "weather" in q or "rain" in q:
            if "tomorrow" in q:
                return WeatherAgentService.get_weather("tomorrow")
            return WeatherAgentService.get_weather("today")

        # --- Calendar ---
        if "today" in q:
            return CalendarAgentService.get_calendar_events("today")
        if "tomorrow" in q:
            return CalendarAgentService.get_calendar_events("tomorrow")

        # --- Shopping list ---
        if "add" in q and ("shopping" in q or ("list" in q and "task" not in q and "todo" not in q)):
            item = cls._extract_item(query)
            if item:
                from models import ShoppingListItem
                ShoppingListItem.create(item_name=item, quantity=1)
                return f"Added '{item}' to your shopping list."

        if words.intersection({"shopping"}) and words.intersection({"show", "list", "what's", "whats"}):
            return cls._show_shopping_list()

        # --- To-do list ---
        if "add" in q and ("todo" in q or "task" in q):
            item = cls._extract_item(query)
            if item:
                from models import Task
                Task.create(task_name=item)
                return f"Added '{item}' to your to-do list."

        if words.intersection({"todo", "tasks"}) and words.intersection({"show", "list", "what's", "whats"}):
            return cls._show_tasks()

        # --- Spotify ---
        if _spotify_intent(q):
            return cls._handle_spotify(query, q)

        # --- Volume (Spotify) ---
        if "volume" in q or "louder" in q or "quieter" in q:
            return cls._handle_volume(q)

        # --- Smart home / lights ---
        if "lights" in q or "led" in q:
            return cls._handle_lights(q)

        return None

    # --- Spotify ---

    @classmethod
    def _handle_spotify(cls, query: str, q: str) -> str:
        from agents.spotify_service import SpotifyService
        words = set(q.split())

        # What's playing?
        if 'playing' in words and words.intersection({"what", "what's", "now", "currently"}):
            return SpotifyService.now_playing()

        # Pause / stop
        if "pause" in q or "stop" in q:
            return SpotifyService.pause()

        # Skip / next
        if "skip" in q or ("next" in q and ("song" in q or "track" in q)):
            return SpotifyService.skip()

        # Previous track
        if "previous" in q or ("last" in q and ("song" in q or "track" in q)):
            return SpotifyService.previous()

        # Play specific thing
        search_query = SpotifyService.extract_search_query(query)
        if search_query:
            return SpotifyService.play_search(search_query)

        # Generic play/resume
        return SpotifyService.play()

    # --- Volume ---

    @classmethod
    def _handle_volume(cls, q: str) -> str:
        from agents.spotify_service import SpotifyService

        # Explicit value always wins, regardless of direction word ("raise to 100%")
        percent = SpotifyService.extract_volume_percent(q)
        if percent is not None:
            return SpotifyService.set_volume(percent)

        if "up" in q or "louder" in q or "raise" in q or "increase" in q:
            current = SpotifyService.get_current_volume()
            if current is None:
                return "Couldn't read current volume."
            return SpotifyService.set_volume(current + VOLUME_STEP)

        if "down" in q or "quieter" in q or "lower" in q or "decrease" in q:
            current = SpotifyService.get_current_volume()
            if current is None:
                return "Couldn't read current volume."
            return SpotifyService.set_volume(current - VOLUME_STEP)

        return "Try 'volume up', 'volume down', or 'set volume to 50%'."

    # --- Timers ---

    @classmethod
    def _handle_timer(cls, query: str, q: str) -> str:
        from agents.timer_service import TimerService

        if "list" in q or "active" in q or "show" in q:
            return TimerService.list_timers()

        seconds = TimerService.parse_duration(query)
        if not seconds:
            return "How long should the timer be? Try 'set a timer for 10 minutes'."

        label = TimerService.extract_label(query)
        return TimerService.start_timer(seconds, label)

    # --- Reminders ---

    @classmethod
    def _handle_reminder(cls, query: str, q: str) -> str:
        time_match = re.search(r'\bat\s+(\d{1,2}):(\d{2})\b', query, re.IGNORECASE)
        if not time_match:
            return "I need a time for the reminder. Try 'remind me at 18:30 to call the vet'."

        hour, minute = int(time_match.group(1)), int(time_match.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return "That doesn't look like a valid time. Use HH:MM format (e.g. 18:30)."

        label_match = re.search(r'\bat\s+\d{1,2}:\d{2}\s+to\s+(.+)$', query, re.IGNORECASE)
        label = label_match.group(1).strip() if label_match else "reminder"

        from services.local_time import get_local_now
        now = get_local_now()
        due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if due <= now:
            due += datetime.timedelta(days=1)

        from models import Task
        Task.create(task_name=f"[reminder] {label}", due_date=due, completed=False)

        time_str = due.strftime('%H:%M')
        day_str = "tomorrow" if due.date() > now.date() else "today"
        return f"Reminder set for {day_str} at {time_str}: {label}."

    # --- Lists ---

    @staticmethod
    def _show_shopping_list() -> str:
        from models import ShoppingListItem
        items = list(ShoppingListItem.select().where(ShoppingListItem.purchased == False))
        if not items:
            return "Your shopping list is empty."
        lines = [
            f"{i.item_name}" + (f" x{i.quantity}" if i.quantity > 1 else "")
            for i in items
        ]
        return "Shopping list: " + ", ".join(lines) + "."

    @staticmethod
    def _show_tasks() -> str:
        from models import Task
        tasks = list(
            Task.select()
            .where(Task.completed == False, ~Task.task_name.startswith('[reminder]'))
            .order_by(Task.due_date)
        )
        if not tasks:
            return "Your to-do list is empty."
        return "To-do: " + ", ".join(t.task_name for t in tasks) + "."

    # --- Lights / smart home ---

    @classmethod
    def _handle_lights(cls, q: str) -> str:
        # LED mode
        if "rainbow" in q or "party" in q:
            change_device_status('led', True)
            from smart_home.smart_home_service import get_device
            device = get_device('led')
            device.mode = 'rainbow'
            device.save()
            return "Rainbow mode on."

        if "normal" in q or "indicator" in q or "default" in q:
            from smart_home.smart_home_service import get_device
            device = get_device('led')
            device.mode = None
            device.save()
            return "Lights set to normal mode."

        if "turn on" in q or ("on" in q and "off" not in q):
            change_device_status('led', True)
            return "Lights turned on."
        if "turn off" in q or "off" in q:
            change_device_status('led', False)
            return "Lights turned off."

        # Fallback: toggle
        device = get_device_status('led')
        change_device_status('led', not device.activated)
        return "Lights toggled."

    # --- Utilities ---

    @staticmethod
    def _extract_item(query: str) -> str | None:
        """Extract the item name from 'add X to Y' or 'add X'."""
        match = re.search(r'\badd\s+(?:"([^"]+)"|(.+))(?:\s+to\s+(.+))?$', query, re.IGNORECASE)
        if match:
            item = (match.group(1) or match.group(2)).strip()
            item = re.sub(
                r'\s+to\s+(the\s+)?(shopping|todo|task)\s*(list)?$',
                '',
                item,
                flags=re.IGNORECASE,
            ).strip()
            return item if item else None
        return None

    @staticmethod
    def _get_time() -> str:
        now = datetime.datetime.now()
        return f"The current time is {now.strftime('%H:%M')}."

    @staticmethod
    def _get_date() -> str:
        today = datetime.date.today()
        return f"Today is {today.strftime('%A, %B %d, %Y')}."


def _spotify_intent(q: str) -> bool:
    """Return True if the query is about Spotify / music playback."""
    music_words = {'play', 'pause', 'resume', 'skip', 'music', 'spotify', 'song', 'track', 'playing', 'previous'}
    return bool(music_words & set(q.split()))
