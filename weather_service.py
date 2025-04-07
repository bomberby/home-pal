from datetime import datetime
from models import WeatherData
import requests

def get_cached_or_fetch(city):
    try:
        return WeatherData.get(WeatherData.city == city)
    except WeatherData.DoesNotExist:
        return fetch_weather_data(city)

def fetch_weather_data(city):
    url = f"https://api.open-meteo.com/v1/forecast?latitude=35.7203484&longitude=139.7831018&hourly=temperature_2m&timezone=auto"
    
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        latitude = data['latitude']
        longitude = data['longitude']
        timezone = data['timezone']
        hourly_temperatures = data['hourly']['temperature_2m']
        first_time = data['hourly']['time'][0]
        
        # Update or create weather data
        try:
            weather_data = WeatherData.get(WeatherData.city == city)
            weather_data.latitude = latitude
            weather_data.longitude = longitude
            weather_data.timezone = timezone
            weather_data.hourly_temperatures = hourly_temperatures
            weather_data.last_updated = datetime.datetime.now()
            weather_data.first_time = first_time
            weather_data.save()
            return weather_data
        except WeatherData.DoesNotExist:
            return WeatherData.create(city=city, latitude=latitude, longitude=longitude, timezone=timezone, hourly_temperatures=hourly_temperatures, first_time=first_time)
        
    else:
        print("Failed to fetch weather data")
        return None
