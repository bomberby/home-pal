from datetime import datetime
from models import WeatherData
from playhouse.shortcuts import model_to_dict
import requests

CACHE_DURATION = 3600
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
    url = f"https://api.open-meteo.com/v1/forecast?latitude={geo['latitude']}&longitude={geo['longitude']}&hourly=temperature_2m,precipitation&timezone=auto"
    
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        latitude = data['latitude']
        longitude = data['longitude']
        timezone = data['timezone']
        hourly_temperatures = data['hourly']['temperature_2m']
        hourly_precipitation = data['hourly']['precipitation']
        first_time = data['hourly']['time'][0]
        
        # Update or create weather data
        try:
            weather_data = WeatherData.get(WeatherData.city == city)
            weather_data.latitude = latitude
            weather_data.longitude = longitude
            weather_data.timezone = timezone
            weather_data.hourly_temperatures = hourly_temperatures
            weather_data.hourly_precipitation = hourly_precipitation
            weather_data.last_updated = datetime.now()
            weather_data.first_time = first_time
            weather_data.save()
            return weather_data
        except WeatherData.DoesNotExist:
            return WeatherData.create(city=city, latitude=latitude, longitude=longitude, timezone=timezone,
                                      hourly_temperatures=hourly_temperatures, hourly_precipitation=hourly_precipitation,
                                      first_time=first_time)
        
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


    # GEO from fallbacks
    match city:
        case 'Ome':
            geo['latitude'] = 35.7902208
            geo['longitude'] = 139.258213
        case 'Osaka':
            geo['latitude'] = 34.6774872
            geo['longitude'] = 135.3212277
        case _:
            # Tokyo
            geo['latitude'] = geo.get('latitude', 35.7203484)
            geo['longitude'] = geo.get('longitude', 139.7831018)
    return geo