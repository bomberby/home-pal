import re
import datetime
from agents.weather_agent_service import WeatherAgentService
from agents.calendar_agent_service import CalendarAgentService
from smart_home.smart_home_service import get_device_status, change_device_status

class AgentService:
    @classmethod
    def handle_query(cls, query: str) -> str:
        """Process the user query and return a text response."""
        q = query.lower().strip()

        if "time" in q:
            return cls._get_time()
        if "date" in q:
            return cls._get_date()
        if "weather" in q or "rain" in q:
            if "tomorrow" in q:
                return WeatherAgentService.get_weather("tomorrow")
            return WeatherAgentService.get_weather("today")
        if "today" in q:
            return CalendarAgentService.get_calendar_events("today")
        if "tomorrow" in q:
            return CalendarAgentService.get_calendar_events("tomorrow")
        if "add" in q and ("shopping" in q or ("list" in q and "task" not in q and "todo" not in q)):
            item = cls._extract_item(query)
            if item:
                from models import ShoppingListItem
                ShoppingListItem.create(item_name=item, quantity=1)
                return f"Added '{item}' to your shopping list."
        if "add" in q and ("todo" in q or "task" in q):
            item = cls._extract_item(query)
            if item:
                from models import Task
                Task.create(task_name=item)
                return f"Added '{item}' to your to-do list."
        if "lights" in q:
            if "turn on" in q or "on" in q and "off" not in q:
                change_device_status('led', True)
                return "Lights turned on."
            if "turn off" in q or "off" in q:
                change_device_status('led', False)
                return "Lights turned off."
            # fallback: toggle
            device = get_device_status('led')
            change_device_status('led', not device.activated)
            return "Lights toggled."

        return None

    @staticmethod
    def _extract_item(query: str) -> str | None:
        """Extract the item name from 'add X to Y' or 'add X'."""
        match = re.search(r'\badd\s+(?:"([^"]+)"|(.+))(?:\s+to\s+(.+))?$', query, re.IGNORECASE)
        if match:
            item = match.group(1).strip()
            # strip trailing "to shopping list" / "to todo" fragments if any slipped through
            item = re.sub(r'\s+to\s+(the\s+)?(shopping|todo|task)\s*(list)?$', '', item, flags=re.IGNORECASE).strip()
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