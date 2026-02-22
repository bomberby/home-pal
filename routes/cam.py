import io
import os
import time
from flask import Blueprint, jsonify, send_file, request
from PIL import Image, ImageEnhance, ImageFilter

cam_bp = Blueprint('cam', __name__)

CAM_DIR = os.path.join('tmp', 'cam')


def _device_dir(device_id: str) -> str:
    return os.path.join(CAM_DIR, device_id)


def _latest_path(device_id: str) -> str:
    return os.path.join(_device_dir(device_id), 'latest.jpg')


# In-memory registry of last-seen timestamps per device
_devices: dict[str, dict] = {}


@cam_bp.route('/cam/<device_id>/snapshot', methods=['POST'])
def receive_snapshot(device_id: str):
    data = request.get_data()
    if not data:
        return jsonify({'error': 'no image data'}), 400

    try:
        img = Image.open(io.BytesIO(data))
        # 1. Denoise: median removes speckle without blurring edges
        img = img.filter(ImageFilter.MedianFilter(size=3))
        # 2. Unsharp mask: restores edge sharpness lost in step 1.
        #    threshold=3 skips noise-level differences, only sharpens real edges.
        img = img.filter(ImageFilter.UnsharpMask(radius=1.5, percent=130, threshold=3))
        # 3. Slight contrast boost — outdoor images from quality-10 JPEG look flat
        img = ImageEnhance.Contrast(img).enhance(1.2)
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=92)
        data = buf.getvalue()
    except Exception as e:
        print(f"[Cam:{device_id}] Post-processing failed, saving raw: {e}")

    os.makedirs(_device_dir(device_id), exist_ok=True)
    with open(_latest_path(device_id), 'wb') as f:
        f.write(data)
    _devices[device_id] = {'last_updated': time.time(), 'bytes': len(data)}
    print(f"[Cam:{device_id}] Snapshot received ({len(data)} bytes)")
    return jsonify({'ok': True})


@cam_bp.route('/cam/<device_id>/image', methods=['GET'])
def get_image(device_id: str):
    path = _latest_path(device_id)
    if not os.path.exists(path):
        return jsonify({'error': 'no image yet'}), 404
    return send_file(path, mimetype='image/jpeg')


@cam_bp.route('/cam/<device_id>/status', methods=['GET'])
def get_status(device_id: str):
    info = _devices.get(device_id)
    if not info:
        return jsonify({'device_id': device_id, 'has_image': False})
    now = time.time()
    return jsonify({
        'device_id': device_id,
        'has_image': os.path.exists(_latest_path(device_id)),
        'last_updated': info['last_updated'],
        'last_updated_seconds_ago': round(now - info['last_updated']),
        'last_image_bytes': info['bytes'],
    })


@cam_bp.route('/cam/', methods=['GET'])
def list_devices():
    now = time.time()
    return jsonify({
        device_id: {
            'last_updated': info['last_updated'],
            'last_updated_seconds_ago': round(now - info['last_updated']),
            'last_image_bytes': info['bytes'],
        }
        for device_id, info in _devices.items()
    })
