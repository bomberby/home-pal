import os
import secrets

class Config:
    SQLALCHEMY_DATABASE_URI = 'sqlite:///site.db'
    _secret = os.environ.get('SECRET_KEY')
    if not _secret:
        _secret = secrets.token_hex(32)
        print("WARNING: SECRET_KEY env var not set — using a random key. Sessions will reset on restart. Set SECRET_KEY to silence this.")
    SECRET_KEY = _secret
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
    VOC_THRESHOLD = 200         # SGP40 scale 0–500; above this the persona reacts
    INDOOR_TEMP_TOO_HOT = 26    # °C
    INDOOR_TEMP_TOO_COLD = 18   # °C
    INDOOR_HUMIDITY_TOO_HIGH = 70  # %