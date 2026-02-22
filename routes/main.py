import re
from flask import Blueprint, render_template, request

main_bp = Blueprint('main', __name__)


def _is_dark(hex_color: str) -> bool:
    """Returns True if the perceived brightness of the hex colour is below 50%."""
    if len(hex_color) == 3:
        hex_color = ''.join(c * 2 for c in hex_color)
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255 < 0.5


@main_bp.route('/')
def index():
    bg = request.args.get('bg', '')
    if not re.fullmatch(r'[0-9a-fA-F]{3,6}', bg):
        bg = None
    dark = _is_dark(bg) if bg else False
    return render_template('index.html', bg=bg, dark=dark)
