from models import SmartHomeDevice
from smart_home.led_enricher_service import LedEnricherService
from playhouse.shortcuts import model_to_dict
from cache import cache


@cache.memoize(timeout=60)
def _build_led_payload(activated: bool, mode: str | None) -> dict:
    """Build the full LED indicator payload. Cached 60s; key includes activated+mode
    so any device state change naturally busts the cache."""
    device = get_device('led')
    return LedEnricherService(device).get_led_state()


def get_device_status(name):
    device = get_device(name)
    if device.name == 'led':
        return _build_led_payload(device.activated, device.mode)
    return device


def get_device(name):
    try:
        device = SmartHomeDevice.get(SmartHomeDevice.name == name)
    except SmartHomeDevice.DoesNotExist:
        device = create_device(name)
    return device


def change_device_status(name, status):
    device = SmartHomeDevice.get(SmartHomeDevice.name == name)
    device.activated = status
    device.save()
    return device


def create_device(name, activated=False, mode=None):
    return SmartHomeDevice.create(
        name=name,
        activated=activated,
        mode=mode
    )
