import json
import os
import time
import threading

import config

SECRETS_PATH = os.path.join('env', 'secrets', 'mqtt.json')
PRESENCE_TIMEOUT = 120      # seconds without update → consider away
PRESENCE_ABSENT_RSSI = -95  # RSSI below this = not in range
PRESENCE_HOME_CONFIRM = 60  # seconds RSSI must stay present before marking as arrived
PRESENCE_AWAY_CONFIRM = 60  # seconds RSSI must stay absent before marking as away


def _load_mqtt_credentials() -> tuple[str, str]:
    try:
        with open(SECRETS_PATH) as f:
            data = json.load(f)
        return data.get('username', ''), data.get('password', '')
    except FileNotFoundError:
        return '', ''
    except Exception as e:
        print(f"[HomeContext] Could not read {SECRETS_PATH}: {e}")
        return '', ''


class HomeContextService:
    # Connection
    _mqtt_connected: bool = False

    # Presence
    _presence_rssi: int = -100
    _presence_battery: float | None = None
    _presence_updated: float = 0.0
    _was_home: bool | None = None   # None = no data yet since startup
    _home_since: float | None = None  # timestamp when present state first seen
    _away_since: float | None = None  # timestamp when absent state first seen
    _welcome_until: float = 0.0

    # Presence transition callbacks
    _on_arrive_callbacks: list = []
    _on_leave_callbacks: list = []

    # Air quality & indoor environment (from AIC topic)
    _voc: float | None = None
    _nox: float | None = None
    _indoor_temp: float | None = None
    _indoor_humidity: float | None = None
    _aic_updated: float = 0.0

    @classmethod
    def register_on_arrive(cls, fn):
        cls._on_arrive_callbacks.append(fn)

    @classmethod
    def register_on_leave(cls, fn):
        cls._on_leave_callbacks.append(fn)

    @classmethod
    def start(cls):
        broker = config.Config.MQTT_BROKER.removeprefix('mqtt://').removeprefix('mqtts://')
        if not broker:
            print("[HomeContext] MQTT_BROKER not set in config — presence/AIC disabled (will always assume home).")
            return
        try:
            import paho.mqtt.client as mqtt  # noqa: F401
        except ImportError:
            print("[HomeContext] paho-mqtt not installed — run: pip install paho-mqtt")
            return

        def on_connect(client, userdata, flags, rc):
            cls._mqtt_connected = (rc == 0)
            print(f"[HomeContext] MQTT connected (rc={rc})")
            client.subscribe(config.Config.PRESENCE_TOPIC)
            client.subscribe(config.Config.AIC_TOPIC)

        def on_disconnect(client, userdata, rc):
            cls._mqtt_connected = False
            if rc != 0:
                print(f"[HomeContext] MQTT disconnected unexpectedly (rc={rc})")

        def on_message(client, userdata, msg):
            try:
                data = json.loads(msg.payload)
                if msg.topic == config.Config.PRESENCE_TOPIC:
                    cls._presence_rssi = data.get('rssi', -100)
                    cls._presence_battery = data.get('battery_percentage')
                    cls._presence_updated = time.time()
                    is_now_home = cls.is_home()
                    if is_now_home:
                        cls._away_since = None  # reset absent timer on any home reading
                        if cls._home_since is None:
                            cls._home_since = time.time()  # start counting presence
                        if cls._was_home is not True and time.time() - cls._home_since >= PRESENCE_HOME_CONFIRM:
                            if cls._was_home is False:  # confirmed away→home transition
                                cls._welcome_until = time.time() + 120  # 2-minute welcome window
                                for fn in cls._on_arrive_callbacks:
                                    threading.Thread(target=fn, daemon=True).start()
                            cls._was_home = True
                    else:
                        cls._home_since = None  # reset present timer on any away reading
                        if cls._away_since is None:
                            cls._away_since = time.time()  # start counting absence
                        if cls._was_home is not False and time.time() - cls._away_since >= PRESENCE_AWAY_CONFIRM:
                            if cls._was_home is True:  # confirmed home→away transition
                                for fn in cls._on_leave_callbacks:
                                    threading.Thread(target=fn, daemon=True).start()
                            cls._was_home = False
                elif msg.topic == config.Config.AIC_TOPIC:
                    cls._aic_updated = time.time()
                    voc = data.get(config.Config.VOC_FIELD)
                    if voc is not None:
                        cls._voc = float(voc)
                    nox = data.get(config.Config.NOX_FIELD)
                    if nox is not None:
                        cls._nox = float(nox)
                    temp = data.get('temperature')
                    if temp is not None:
                        cls._indoor_temp = float(temp)
                    humidity = data.get('humidity')
                    if humidity is not None:
                        cls._indoor_humidity = float(humidity)
            except Exception:
                pass

        def _run():
            import paho.mqtt.client as mqtt
            username, password = _load_mqtt_credentials()
            while True:
                client = mqtt.Client()
                client.on_connect = on_connect
                client.on_disconnect = on_disconnect
                client.on_message = on_message
                if username:
                    client.username_pw_set(username, password)
                try:
                    client.connect(broker, config.Config.MQTT_PORT, 60)
                    client.loop_forever()
                except Exception as e:
                    print(f"[HomeContext] MQTT error: {e}, retrying in 30s")
                    time.sleep(30)

        threading.Thread(target=_run, daemon=True).start()

    @classmethod
    def is_connected(cls) -> bool:
        return cls._mqtt_connected

    @classmethod
    def is_home(cls) -> bool:
        if not config.Config.MQTT_BROKER:
            return True  # presence disabled → always assume home
        if time.time() - cls._presence_updated > PRESENCE_TIMEOUT:
            return False
        return cls._presence_rssi > PRESENCE_ABSENT_RSSI

    @classmethod
    def is_just_arrived(cls) -> bool:
        return time.time() < cls._welcome_until

    @classmethod
    def air_quality(cls) -> str | None:
        """Return 'good', 'poor', or 'alert' based on VOC index, or None if no data yet."""
        if cls._voc is None:
            return None
        if cls._voc > config.Config.VOC_THRESHOLD:
            return 'alert'
        if cls._voc > config.Config.VOC_POOR_THRESHOLD:
            return 'poor'
        return 'good'

    @classmethod
    def has_poor_air(cls) -> bool:
        return cls.air_quality() in ('poor', 'alert')

    @classmethod
    def indoor_discomfort(cls) -> str | None:
        """Returns 'indoor_hot', 'indoor_cold', 'indoor_humid', or None."""
        t = cls._indoor_temp
        h = cls._indoor_humidity
        if t is not None and t > config.Config.INDOOR_TEMP_TOO_HOT:
            return 'indoor_hot'
        if t is not None and t < config.Config.INDOOR_TEMP_TOO_COLD:
            return 'indoor_cold'
        if h is not None and h > config.Config.INDOOR_HUMIDITY_TOO_HIGH:
            return 'indoor_humid'
        return None
