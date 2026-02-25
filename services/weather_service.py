from datetime import datetime
from models import WeatherData, WeatherLocation
from playhouse.shortcuts import model_to_dict
from config import Config
import requests
import json

CACHE_DURATION = 3600

# WMO Weather interpretation codes — https://open-meteo.com/en/docs
_WMO_DESCRIPTIONS = {
    0:  "clear sky",
    1:  "mainly clear",
    2:  "partly cloudy",
    3:  "overcast",
    45: "foggy",
    48: "foggy",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    56: "freezing drizzle",
    57: "heavy freezing drizzle",
    61: "light rain",
    63: "moderate rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "heavy freezing rain",
    71: "light snow",
    73: "moderate snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light showers",
    81: "moderate showers",
    82: "heavy showers",
    85: "snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}

def wmo_description(code: int) -> str:
    """Full human-readable WMO weather condition (e.g. 'light rain')."""
    return _WMO_DESCRIPTIONS.get(int(code), "")

def wmo_label(code: int) -> str:
    """Short display label for space-constrained contexts (e.g. e-ink display)."""
    code = int(code)
    if code == 0:                                                return "Sun"
    if code <= 2:                                                return "P.Cld"
    if code == 3:                                                return "Cloudy"
    if code in (45, 48):                                         return "Fog"
    if 51 <= code <= 57:                                         return "Drzl"
    if (61 <= code <= 67) or (80 <= code <= 82):                 return "Rain"
    if (71 <= code <= 77) or (85 <= code <= 86):                 return "Snow"
    if code >= 95:                                               return "Storm"
    return ""

def wmo_category(code: int) -> int:
    """Map WMO code to severity category 0–7 (used for icon selection on the e-ink display)."""
    code = int(code)
    if code == 0:                                                return 0  # clear
    if code <= 2:                                                return 1  # mainly clear / partly cloudy
    if code == 3:                                                return 2  # overcast
    if code in (45, 48):                                         return 3  # fog
    if 51 <= code <= 57:                                         return 4  # drizzle
    if (61 <= code <= 67) or (80 <= code <= 82):                 return 5  # rain
    if (71 <= code <= 77) or (85 <= code <= 86):                 return 6  # snow
    if code >= 95:                                               return 7  # thunderstorm
    return 0

def get_hourly_forecast(city: str, count: int = 24) -> dict | None:
    """
    Return `count` hours of forecast data starting from the current hour, with codes resolved.

    Returns {'temps', 'precips', 'condition_labels', 'condition_descriptions'} or None on failure.
    condition_labels   — short strings for space-constrained display (e.g. 'Rain')
    condition_descriptions — full strings for natural language (e.g. 'moderate rain')
    """
    try:
        city_data = get_cached_or_fetch([city]).get(city)
        if not city_data:
            return None
        temps   = json.loads(city_data.get('hourly_temperatures', '[]'))
        precips = json.loads(city_data.get('hourly_precipitation', '[]'))
        codes   = json.loads(city_data.get('hourly_weathercodes', '[]'))
        first_time = datetime.fromisoformat(city_data['first_time'])
        offset = max(0, int((datetime.now() - first_time).total_seconds() // 3600))
        s, e = offset, offset + count
        return {
            'temps':                  temps[s:e],
            'precips':                precips[s:e],
            'condition_labels':       [wmo_label(c)       for c in codes[s:e]],
            'condition_descriptions': [wmo_description(c) for c in codes[s:e]],
        }
    except Exception:
        return None

def get_default_location():
    try:
        loc = WeatherLocation.get(WeatherLocation.is_default == True)
        return loc.location_name
    except WeatherLocation.DoesNotExist:
        pass
    first = WeatherLocation.select().first()
    if first:
        return first.location_name
    return Config.WEATHER_LOCATION

def get_cached_or_fetch(cities):
    weather_data_dict = {}
    for city in cities:
        try:
            weather_data = WeatherData.get(WeatherData.city == city)
            if (datetime.now() - weather_data.last_updated).total_seconds() > CACHE_DURATION:
                weather_data_dict[city] = model_to_dict(fetch_weather_data(city))
            else:
                weather_data_dict[city] = model_to_dict(weather_data)
        except WeatherData.DoesNotExist:
            weather_data_dict[city] = model_to_dict(fetch_weather_data(city))
    return weather_data_dict

def fetch_weather_data(city):
    geo = geo_from_city_name(city)
    url = f"https://api.open-meteo.com/v1/forecast?latitude={geo['latitude']}&longitude={geo['longitude']}&hourly=temperature_2m,precipitation,weathercode&timezone=auto"

    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        latitude = data['latitude']
        longitude = data['longitude']
        timezone = data['timezone']
        hourly_temperatures = json.dumps(data['hourly']['temperature_2m'])
        hourly_precipitation = json.dumps(data['hourly']['precipitation'])
        hourly_weathercodes = json.dumps(data['hourly'].get('weathercode', []))
        first_time = data['hourly']['time'][0]

        # Update or create weather data
        try:
            weather_data = WeatherData.get(WeatherData.city == city)
            weather_data.latitude = latitude
            weather_data.longitude = longitude
            weather_data.timezone = timezone
            weather_data.hourly_temperatures = hourly_temperatures
            weather_data.hourly_precipitation = hourly_precipitation
            weather_data.hourly_weathercodes = hourly_weathercodes
            weather_data.last_updated = datetime.now()
            weather_data.first_time = first_time
            weather_data.save()
            return weather_data
        except WeatherData.DoesNotExist:
            return WeatherData.create(city=city, latitude=latitude, longitude=longitude, timezone=timezone,
                                      hourly_temperatures=hourly_temperatures, hourly_precipitation=hourly_precipitation,
                                      hourly_weathercodes=hourly_weathercodes, first_time=first_time)
        
    else:
        print("Failed to fetch weather data")
        return None

def geo_from_city_name(city):
    geo = {}
    try:
        weather_data = WeatherData.get(WeatherData.city == city)
        geo['latitude'] = weather_data.latitude
        geo['longitude'] = weather_data.longitude
        return geo
    except WeatherData.DoesNotExist:
        pass

    # GEO FROM ONLINE LOOKUP
    try:
        response = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "en", "format": "json"},
            timeout=5
        )
        if response.status_code == 200:
            results = response.json().get("results", [])
            if results:
                geo['latitude'] = results[0]['latitude']
                geo['longitude'] = results[0]['longitude']
                return geo
    except Exception as e:
        print(f"Geocoding lookup failed for '{city}': {e}")

    # GEO from fallbacks (last resort)
    match city:
        case 'Ome':
            geo['latitude'] = 35.7902208
            geo['longitude'] = 139.258213
        case 'Osaka':
            geo['latitude'] = 34.6774872
            geo['longitude'] = 135.3212277
        case 'Tel Aviv':
            geo['latitude'] = 32.0943305
            geo['longitude'] = 34.8013659
        case _:
            geo['latitude'] = 35.7203484
            geo['longitude'] = 139.7831018
    return geo