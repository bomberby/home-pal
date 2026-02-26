import os
import secrets
from pathlib import Path

_SECRET_KEY_PATH = Path('env/secrets/secret_key.txt')

def _resolve_secret_key() -> str:
    if key := os.environ.get('SECRET_KEY'):
        return key
    if _SECRET_KEY_PATH.exists():
        return _SECRET_KEY_PATH.read_text().strip()
    key = secrets.token_hex(32)
    _SECRET_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SECRET_KEY_PATH.write_text(key)
    return key

class Config:
    SQLALCHEMY_DATABASE_URI = 'sqlite:///site.db'
    SECRET_KEY = _resolve_secret_key()
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1' # Allow local network oauth for calendar
    DEBUG = True
    JSON_AS_ASCII = False
    WEATHER_LOCATION = 'Tokyo'
    TRAIN_STATION_URL = "https://www.jreast-timetable.jp/en/timetable/list0303.html"

    # MQTT / smart home
    MQTT_BROKER = 'homeassistant.local'
    MQTT_PORT = 1883
    PRESENCE_TOPIC = 'workroom/ble_scanner/esp_c6_leds/attributes'
    AIC_TOPIC = 'zigbee2mqtt/ESP32-C6-Weather-Display'
    VOC_FIELD = 'voc_index_13'
    NOX_FIELD = 'nox_index_14'
    VOC_POOR_THRESHOLD  = 200   # SGP40/SGP41 index scale; 100 is baseline, above 200 is 'poor'
    VOC_THRESHOLD = 400         # above this air quality is 'alert' (persona reacts)
    NOX_POOR_THRESHOLD  = 50    # SGP41 NOx index scale; 1 is excellent, 100 is baseline, above 50 is 'poor'
    NOX_THRESHOLD = 100         # above this NOx level is 'alert'
    INDOOR_TEMP_TOO_HOT = 26    # °C
    INDOOR_TEMP_TOO_COLD = 18   # °C
    INDOOR_HUMIDITY_TOO_HIGH = 70  # %