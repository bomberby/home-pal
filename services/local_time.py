"""Local wall-clock time, derived from the default weather location's IANA timezone.

The timezone string is stored in WeatherData by Open-Meteo (e.g. 'Asia/Tokyo').
It is cached in memory for up to one hour so every call doesn't hit the DB.
Falls back to UTC if no weather data exists yet.
"""
from datetime import datetime
from zoneinfo import ZoneInfo
import time

_tz_cache: tuple[str, float] | None = None  # (iana_tz, monotonic_fetched_at)
_TZ_CACHE_TTL = 3600


def get_local_now() -> datetime:
    """Return the current time as a naive datetime in the local timezone."""
    global _tz_cache
    now_mono = time.monotonic()
    if _tz_cache is None or (now_mono - _tz_cache[1]) > _TZ_CACHE_TTL:
        _tz_cache = (_resolve_tz(), now_mono)
    return datetime.now(ZoneInfo(_tz_cache[0])).replace(tzinfo=None)


def _resolve_tz() -> str:
    try:
        from models import WeatherData
        from services.weather_service import get_default_location
        city = get_default_location()
        wd = WeatherData.get(WeatherData.city == city)
        if wd.timezone:
            return wd.timezone
    except Exception:
        pass
    return 'UTC'
