from flask import Blueprint, jsonify, request
from playhouse.shortcuts import model_to_dict
from models import BaseModel
from smart_home.smart_home_service import get_device, get_device_status, change_device_status

smart_home_bp = Blueprint('smart_home', __name__)


@smart_home_bp.route("/sh/<device_name>", methods=["GET"])
def device_status(device_name):
    device = get_device_status(device_name)
    device = model_to_dict(device) if isinstance(device, BaseModel) else device
    return jsonify(device)


@smart_home_bp.route("/sh/<device_name>", methods=["POST"])
def device_status_post(device_name):
    device = get_device_status(device_name)
    activated = request.json.get('activated')
    if activated is not None:
        device = change_device_status(device_name, bool(activated))
    device = model_to_dict(device) if isinstance(device, BaseModel) else device
    return jsonify(device)


@smart_home_bp.route("/sh/<device_name>/on", methods=["GET"])
def device_turn_on(device_name):
    device = change_device_status(device_name, True)
    device = model_to_dict(device) if isinstance(device, BaseModel) else device
    return jsonify(device)


@smart_home_bp.route("/sh/<device_name>/off", methods=["GET"])
def device_turn_off(device_name):
    device = change_device_status(device_name, False)
    return jsonify(model_to_dict(device))


@smart_home_bp.route("/sh/<device_name>/toggle", methods=["GET"])
def device_toggle(device_name):
    device = get_device(device_name)
    device = change_device_status(device_name, not device.activated)
    return jsonify(model_to_dict(device))


@smart_home_bp.route("/sh/<device_name>/mode/<mode>", methods=["GET"])
def device_change_mode(device_name, mode):
    device = get_device(device_name)
    device.mode = mode
    device.save()
    return jsonify(model_to_dict(device))


@smart_home_bp.route("/sh/<device_name>/mode/<mode>/toggle", methods=["GET"])
def device_toggle_mode(device_name, mode):
    device = get_device(device_name)
    if device.activated == False:
        device.activated = True
        device.mode = None
    if device.mode == mode:
        device.mode = None
    else:
        device.mode = mode
    device.save()
    return jsonify(model_to_dict(device))
