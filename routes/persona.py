import re
import threading
from flask import Blueprint, jsonify, send_file, render_template, request
from agents.persona_agent import PersonaAgent
from services.image_gen_service import ImageGenService

persona_bp = Blueprint('persona', __name__)


@persona_bp.route('/persona', methods=['GET'])
def get_persona():
    state_data = PersonaAgent.get_current_state()
    state = state_data['state']
    if state == 'absent':
        return jsonify({'state': 'absent', 'image_url': None, 'quote': None, 'generating': False})
    cached = ImageGenService.get_cached(state)
    quote = state_data.get('quote', '')
    if cached:
        return jsonify({'state': state, 'image_url': f'/persona/image/{state}', 'quote': quote, 'generating': False})
    if state not in ImageGenService._in_progress:
        threading.Thread(target=ImageGenService.generate, args=(state, state_data['prompt']), daemon=True).start()
    return jsonify({'state': state, 'image_url': None, 'quote': quote, 'generating': True})


@persona_bp.route('/persona/widget')
def persona_widget():
    bg = request.args.get('bg', '1a1a2e')
    if not re.fullmatch(r'[0-9a-fA-F]{3,6}', bg):
        bg = '1a1a2e'
    return render_template('persona_widget.html', bg=bg)


@persona_bp.route('/persona/image/<state>', methods=['GET'])
def get_persona_image(state):
    if not re.fullmatch(r'[a-z_]+', state):
        return jsonify({'error': 'Invalid state'}), 400
    path = ImageGenService.get_cached(state)
    if not path:
        return jsonify({'error': 'Image not yet generated'}), 404
    return send_file(path, mimetype='image/png')
