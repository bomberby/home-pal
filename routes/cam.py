import io
import os
import time
from flask import Blueprint, jsonify, send_file, request, render_template
from PIL import Image, ImageEnhance, ImageFilter

cam_bp = Blueprint('cam', __name__)

CAM_DIR = os.path.join('tmp', 'cam')
STALE_SECONDS = 120


def _device_dir(device_id: str) -> str:
    return os.path.join(CAM_DIR, device_id)


def _latest_path(device_id: str) -> str:
    return os.path.join(_device_dir(device_id), 'latest.jpg')


# In-memory registry of last-seen timestamps per device
_devices: dict[str, dict] = {}


def _status_for(device_id: str) -> dict:
    """Build status dict, falling back to file mtime on server restart."""
    path = _latest_path(device_id)
    has_image = os.path.exists(path)
    now = time.time()
    info = _devices.get(device_id)

    if info:
        age = round(now - info['last_updated'])
    elif has_image:
        age = round(now - os.path.getmtime(path))
    else:
        return {'device_id': device_id, 'has_image': False, 'stale': True}

    return {
        'device_id': device_id,
        'has_image': has_image,
        'stale': age > STALE_SECONDS,
        'last_updated_seconds_ago': age,
    }


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
    status = _status_for(device_id)
    if not status['has_image']:
        return jsonify({'error': 'no image yet'}), 404
    if status['stale']:
        return jsonify({'error': 'image is stale'}), 404
    return send_file(_latest_path(device_id), mimetype='image/jpeg')


@cam_bp.route('/cam/<device_id>/status', methods=['GET'])
def get_status(device_id: str):
    return jsonify(_status_for(device_id))


@cam_bp.route('/cam/<device_id>/widget')
def cam_widget(device_id: str):
    bg = request.args.get('bg', '111827')
    if not bg.replace('#', '').isalnum():
        bg = '111827'
    return render_template('cam_widget.html', device_id=device_id, bg=bg)


@cam_bp.route('/cam/', methods=['GET'])
def list_devices():
    """List all known devices — includes file-based entries for server-restart resilience."""
    result = {}
    now = time.time()

    for device_id, info in _devices.items():
        age = round(now - info['last_updated'])
        result[device_id] = {
            'last_updated_seconds_ago': age,
            'stale': age > STALE_SECONDS,
        }

    # Devices with image files but no in-memory state (server restart)
    if os.path.isdir(CAM_DIR):
        for entry in os.scandir(CAM_DIR):
            if entry.is_dir() and entry.name not in result:
                path = os.path.join(entry.path, 'latest.jpg')
                if os.path.exists(path):
                    age = round(now - os.path.getmtime(path))
                    result[entry.name] = {
                        'last_updated_seconds_ago': age,
                        'stale': age > STALE_SECONDS,
                    }

    return jsonify(result)
