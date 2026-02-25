"""Shared calendar event utilities used across agents and services."""
import datetime


def parse_dt(iso_string: str) -> datetime.datetime:
    """Parse an ISO 8601 datetime string, correctly handling the Z timezone suffix."""
    return datetime.datetime.fromisoformat(iso_string.replace('Z', '+00:00'))


def event_date(event: dict) -> str | None:
    """Extract the local date string (YYYY-MM-DD) from a calendar event's start field.

    Works for both timed events (start.dateTime) and all-day events (start.date).
    """
    raw = event.get('start', {}).get('dateTime') or event.get('start', {}).get('date')
    if not raw:
        return None
    try:
        return parse_dt(raw).astimezone().strftime('%Y-%m-%d')
    except (ValueError, AttributeError):
        return raw  # already a bare date string (YYYY-MM-DD)


def is_event_on(event: dict, date_str: str) -> bool:
    """Return True if the event falls on the given local date (YYYY-MM-DD)."""
    return event_date(event) == date_str


def event_label(event: dict) -> str:
    """Return a human-readable label for a calendar event.

    Uses the event summary when available. For untitled events from a work
    calendar, returns 'work meeting' so the LLM has useful context rather than
    '(untitled event)'. Work calendars frequently omit titles for privacy.
    """
    summary = (event.get('summary') or '').strip()
    if summary:
        return summary
    if event.get('calendar_purpose') == 'work':
        return 'work meeting'
    return '(untitled event)'
