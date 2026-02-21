import datetime
import json
from services.weather_service import get_cached_or_fetch, get_default_location

class WeatherAgentService:
  @staticmethod
  def get_weather(day: str) -> str:
      """
      Get weather for today or tomorrow.
      """
      if day == "today":
          return WeatherAgentService._get_weather_today()
      elif day == "tomorrow":
          return WeatherAgentService._get_weather_tomorrow()
      return "Sorry, I couldn't fetch the weather for that day."

  @staticmethod
  def _get_weather_today() -> str:
    try:
      precipitation, temperatures, first_time = WeatherAgentService._fetch_weather_data()
      now = datetime.datetime.now()
      start, end = WeatherAgentService._extract_today_window(first_time, now)

      today_precip = precipitation[start:end]
      today_temps = temperatures[start:end]

      temp_summary = WeatherAgentService._summarize_temperature(today_temps)
      rain_summary = WeatherAgentService._summarize_precipitation(today_precip, now)

      return f"Today's weather: {temp_summary}. {rain_summary}"

    except Exception as e:
      return f"Error fetching today's weather: {str(e)}"

  @staticmethod
  def _get_weather_tomorrow() -> str:
    try:
      precipitation, temperatures, first_time = WeatherAgentService._fetch_weather_data()
      now = datetime.datetime.now()

      # Calculate tomorrow’s base index and slice
      hours_since_start = int((now - first_time).total_seconds() // 3600)
      tomorrow_start_index = hours_since_start + (24 - now.hour)
      tomorrow_end_index = tomorrow_start_index + 24

      tomorrow_precip = precipitation[tomorrow_start_index:tomorrow_end_index]
      tomorrow_temps = temperatures[tomorrow_start_index:tomorrow_end_index]

      # Base time for rain calculation should be midnight tomorrow
      midnight_tomorrow = (now + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
      )

      temp_summary = WeatherAgentService._summarize_temperature(tomorrow_temps)
      rain_summary = WeatherAgentService._summarize_precipitation(tomorrow_precip, midnight_tomorrow)

      return f"Tomorrow's weather: {temp_summary}. {rain_summary}"

    except Exception as e:
      return f"Error fetching tomorrow's weather: {str(e)}"

  @staticmethod
  def _fetch_weather_data():
      location = get_default_location()
      weather_data = get_cached_or_fetch([location])
      city_data = weather_data.get(location)

      if not city_data:
          raise Exception(f"Weather data for {location} is unavailable.")

      precipitation = json.loads(city_data["hourly_precipitation"])
      temperatures = json.loads(city_data["hourly_temperatures"])
      first_time = datetime.datetime.fromisoformat(city_data["first_time"])

      return precipitation, temperatures, first_time

  @staticmethod
  def _extract_today_window(first_time: datetime.datetime, now: datetime.datetime):
      hours_since_start = int((now - first_time).total_seconds() // 3600)
      hours_remaining_today = 24 - now.hour
      end_index = hours_since_start + hours_remaining_today
      return hours_since_start, end_index

  @staticmethod
  def _extract_tomorrow_window(first_time: datetime.datetime, tomorrow_start: datetime.datetime, tomorrow_end: datetime.datetime):
      hours_since_start = int((tomorrow_start - first_time).total_seconds() // 3600)
      hours_remaining_tomorrow = int((tomorrow_end - tomorrow_start).total_seconds() // 3600)
      return hours_since_start, hours_since_start + hours_remaining_tomorrow

  @staticmethod
  def _summarize_temperature(temps: list) -> str:
      max_temp = max(temps)
      min_temp = min(temps)

      if max_temp >= 22:
          category = "a warm day"
      elif max_temp <= 12:
          category = "a cold day"
      else:
          category = "a mild day"

      return (
          f"It will be {category}, with a high of {round(max_temp, 1)}°C "
          f"and a low of {round(min_temp, 1)}°C"
      )

  @staticmethod
  def _summarize_precipitation(precip: list, base_time: datetime.datetime) -> str:
    will_rain = any(p > 0.1 for p in precip)
    if not will_rain:
      return "No rain is expected."

    rain_start_index = next((i for i, p in enumerate(precip) if p > 0.1), None)
    if rain_start_index is not None:
      rain_time = base_time + datetime.timedelta(hours=rain_start_index)
      formatted_rain_time = rain_time.strftime('%H:%M')
      return f"Rain is expected starting around {formatted_rain_time}."

    return "Rain is expected later."


    
