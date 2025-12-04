import google_calender
import datetime

class CalendarAgentService:
  @staticmethod
  def get_calendar_events(day):
    """
    Fetch events for today or tomorrow from the calendar service.
    Assumes the calendar service is already set up with required endpoints.
    """
    # Get the current date and tomorrow's date in ISO format
    today = datetime.datetime.now()
    tomorrow = today + datetime.timedelta(days=1)

    # Format the dates to compare with event start times
    today_str = today.strftime('%Y-%m-%d')
    tomorrow_str = tomorrow.strftime('%Y-%m-%d')

    # Get all events from the calendar service (replace this with your service call)
    events = google_calender.get_all_events()  # Fetch all events from your service

    # Filter events based on the requested day (today or tomorrow)
    if day == "today":
        events = [event for event in events if CalendarAgentService._is_event_today(event, today_str)]
    elif day == "tomorrow":
        events = [event for event in events if CalendarAgentService._is_event_tomorrow(event, tomorrow_str)]

    if not events:
        return f"You have no events for {day}."

    # Format the events for TTS
    events_text = f"Here are your events for {day}: "
    events_text += CalendarAgentService._format_events(events)
    return events_text

  @staticmethod
  def _is_event_today(event, today_str):
    """
    Check if the event occurs today.
    """
    event_start = event['start'].get('dateTime', event['start'].get('date'))
    event_date = CalendarAgentService._extract_event_date(event_start)
    return event_date == today_str

  @staticmethod
  def _is_event_tomorrow(event, tomorrow_str):
    """
    Check if the event occurs tomorrow.
    """
    event_start = event['start'].get('dateTime', event['start'].get('date'))
    event_date = CalendarAgentService._extract_event_date(event_start)
    return event_date == tomorrow_str

  @staticmethod
  def _extract_event_date(event_start):
    """
    Extract just the date from the event's start time.
    """
    try:
      event_time = datetime.datetime.fromisoformat(event_start.replace("Z", "+00:00"))
      return event_time.strftime('%Y-%m-%d')  # Return only the date part
    except ValueError:
      return event_start  # If it's just a date (no time), return as-is

  @staticmethod
  def _format_events(events):
    """
    Format the events for TTS output.
    """
    formatted_events = []
    
    for event in events:
      event_title = event.get('summary', 'No title')
      event_start = event['start'].get('dateTime', event['start'].get('date'))
      event_end = event['end'].get('dateTime', event['end'].get('date'))

      # Convert the event start and end times to a human-readable format
      start_time = CalendarAgentService._convert_to_human_time(event_start)
      end_time = CalendarAgentService._convert_to_human_time(event_end)

      # Check if event spans multiple days
      is_multiple_days = CalendarAgentService._is_multiple_days(event_start, event_end)

      # Prepare the event details
      event_details = f"{event_title} from {start_time} to {end_time}"

      # If an event spans multiple days
      if is_multiple_days:
        event_details += f" (spanning multiple days)"

      formatted_events.append(event_details)

    # Join all event details together
    return ", ".join(formatted_events)


  @staticmethod
  def _convert_to_human_time(date_str):
    """
    Convert a date-time string to a human-readable format.
    """
    try:
      # Convert the date string to a datetime object
      event_time = datetime.datetime.fromisoformat(date_str.replace("Z", "+00:00"))
      return event_time.strftime('%I:%M %p')  # Format as 10:00 AM, etc.
    except ValueError:
      return date_str  # If it's just a date (no time), return as-is
    
  @staticmethod
  def _is_multiple_days(start, end):
    """
    Check if the event spans multiple days.
    """
    try:
      start_time = datetime.datetime.fromisoformat(start.replace("Z", "+00:00"))
      end_time = datetime.datetime.fromisoformat(end.replace("Z", "+00:00"))
      return start_time.date() != end_time.date()
    except ValueError:
      return False  # If the event doesn't have proper datetime formatting, it's not spanning days
