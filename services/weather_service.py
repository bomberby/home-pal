from datetime import datetime
from models import WeatherData, WeatherLocation
from playhouse.shortcuts import model_to_dict
from config import Config
import requests
import json

CACHE_DURATION = 3600

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