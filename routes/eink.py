import io
from flask import Blueprint, send_file
from services.image_dither import dither_image, dither_bw_image

eink_bp = Blueprint('eink', __name__)


@eink_bp.route('/image.bin')
def get_image():
    black_and_white = True
    if black_and_white:
        img_io = dither_bw_image()
        return send_file(img_io, mimetype='image/bmp')
    else:
        quantized = dither_image()
        pixels = list(quantized.getdata())
        packed_bytes = bytearray()
        for i in range(0, len(pixels), 2):
            byte_val = ((pixels[i] & 0x0F) << 4) | (pixels[i+1] & 0x0F)
            packed_bytes.append(byte_val)
        return send_file(io.BytesIO(packed_bytes), mimetype='application/octet-stream')
