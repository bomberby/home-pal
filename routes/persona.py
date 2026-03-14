import re
from flask import Blueprint, jsonify, send_file, render_template, request
from agents.persona.agent import PersonaAgent
from agents.image_gen_service import ImageGenService

persona_bp = Blueprint('persona', __name__)


@persona_bp.route('/persona', methods=['GET'])
def get_persona():
    if PersonaAgent.is_absent():
        return jsonify({'state': 'absent', 'image_url': None, 'quote': None, 'suggestion': None, 'generating': False})
    state_data = PersonaAgent.get_current_state()
    state = state_data['state']
    quote = state_data.get('quote', '')
    suggestion = state_data.get('suggestion')
    prompt = state_data.get('prompt')
    if prompt:
        image_path, generating = PersonaAgent.get_state_image(state, prompt)
        image_url = f'/persona/image/{state}' if image_path else None
    else:
        image_path = PersonaAgent.get_current_image()
        generating = False
        if image_path:
            from pathlib import Path
            image_url = f'/persona/image/{Path(image_path).stem}'
        else:
            image_url = None
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


@persona_bp.route('/persona/desktop')
def persona_desktop():
    return render_template('persona_desktop.html')


@persona_bp.route('/persona/chat', methods=['POST'])
def persona_chat():
    data  = request.get_json(silent=True) or {}
    query = (data.get('query') or '').strip()
    if not query:
        return jsonify({'error': 'query is required'}), 400

    from agents.chat_service import ChatService
    result = ChatService.handle(query, data.get('history') or [])

    image_url = None
    try:
        from pathlib import Path
        path = PersonaAgent.get_image_for_mood(result['reply'], blocking=False)
        if path:
            image_url = f'/persona/image/{Path(path).stem}'
    except Exception as e:
        print(f'[persona_chat] image error: {e}')

    return jsonify({'reply': result['reply'], 'image_url': image_url})
