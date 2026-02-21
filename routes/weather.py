from flask import Blueprint, jsonify, request
from models import WeatherData, WeatherLocation
from config import Config
from services.weather_service import get_cached_or_fetch

weather_bp = Blueprint('weather', __name__)


@weather_bp.route('/weather', methods=['GET'])
def get_weather():
    try:
        locations = [item.location_name for item in WeatherLocation.select()]
        if not locations:
            locations = [Config.WEATHER_LOCATION]
        weather_data_list = get_cached_or_fetch(locations)
        return jsonify(weather_data_list)
    except WeatherData.DoesNotExist:
        return jsonify({'error': 'Weather data not found'}), 404


@weather_bp.route('/weather-locations', methods=['GET'])
def get_weather_locations():
    locations = [
        {'location_name': item.location_name, 'is_default': item.is_default}
        for item in WeatherLocation.select()
    ]
    return jsonify(locations)


@weather_bp.route('/weather-locations', methods=['POST'])
def add_weather_location():
    data = request.json
    WeatherLocation.get_or_create(location_name=data['location_name'])
    return jsonify({'location_name': data['location_name']})


@weather_bp.route('/weather-locations/<location_name>', methods=['DELETE'])
def delete_weather_location(location_name):
    try:
        item = WeatherLocation.get(WeatherLocation.location_name == location_name)
        item.delete_instance()
        return jsonify({'message': 'Deleted'}), 200
    except WeatherLocation.DoesNotExist:
        return jsonify({'error': 'Not found'}), 404


@weather_bp.route('/weather-locations/<location_name>/set-default', methods=['POST'])
def set_default_weather_location(location_name):
    WeatherLocation.update(is_default=False).execute()
    try:
        item = WeatherLocation.get(WeatherLocation.location_name == location_name)
        item.is_default = True
        item.save()
        return jsonify({'location_name': location_name, 'is_default': True})
    except WeatherLocation.DoesNotExist:
        return jsonify({'error': 'Not found'}), 404
