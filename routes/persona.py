import re
from flask import Blueprint, jsonify, send_file, render_template, request
from agents.persona_agent import PersonaAgent
from agents.image_gen_service import ImageGenService

persona_bp = Blueprint('persona', __name__)


@persona_bp.route('/persona', methods=['GET'])
def get_persona():
    state_data = PersonaAgent.get_current_state()
    state = state_data['state']
    if state == 'absent':
        return jsonify({'state': 'absent', 'image_url': None, 'quote': None, 'suggestion': None, 'generating': False})
    quote = state_data.get('quote', '')
    suggestion = state_data.get('suggestion')
    image_path, generating = PersonaAgent.get_state_image(state, state_data['prompt'])
    image_url = f'/persona/image/{state}' if image_path else None
    return jsonify({'state': state, 'image_url': image_url, 'quote': quote, 'suggestion': suggestion, 'generating': generating})


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
    tier = request.args.get('tier')
    if tier == 'fast':
        from agents.image_gen_service import OUTPUT_DIR
        path = OUTPUT_DIR / f"{state}.png"
    elif tier == 'mq':
        path = ImageGenService._hq_path(state)
    elif tier == 'uhq':
        path = ImageGenService._uhq_path(state)
    elif tier in ('fast_exp', 'mq_exp', 'uhq_exp'):
        from agents.image_gen_service import OUTPUT_DIR
        path = OUTPUT_DIR / f"{state}_{tier}.png"
    else:
        path = ImageGenService.get_cached(state)
    if not path or not path.exists():
        return jsonify({'error': 'Image not found'}), 404
    return send_file(path, mimetype='image/png')
