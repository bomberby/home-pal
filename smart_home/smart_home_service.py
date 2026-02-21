from models import SmartHomeDevice
from smart_home.led_enricher_service import LedEnricherService
from playhouse.shortcuts import model_to_dict

def get_device_status(name):
  device = get_device(name)
  if device.name == 'led':
    service = LedEnricherService(device)
    return service.get_led_state()

  return device

def get_device(name):
  try:
    device = SmartHomeDevice.get(SmartHomeDevice.name == name)
  except SmartHomeDevice.DoesNotExist:
    print('creating device')
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