import datetime
from agents.weather_agent_service import WeatherAgentService
from agents.calendar_agent_service import CalendarAgentService
from smart_home_service import get_device_status, change_device_status

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
        if "lights" in q:
            device = get_device_status('led')
            device = change_device_status('led', not device.activated)

        # return "Sorry, I don't understand that question."
        return None

    @staticmethod
    def _get_time() -> str:
        now = datetime.datetime.now()
        return f"The current time is {now.strftime('%H:%M')}."

    @staticmethod
    def _get_date() -> str:
        today = datetime.date.today()
        return f"Today is {today.strftime('%A, %B %d, %Y')}."