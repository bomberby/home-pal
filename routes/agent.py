from flask import Blueprint, jsonify, request, send_file
from services.tts_service import generate_speech_audio
from agents.agent_service import AgentService

agent_bp = Blueprint('agent', __name__)


@agent_bp.route('/tts/speak', methods=['POST'])
def speak_text():
    try:
        data = request.json
        text = data.get("text", "")
        if not text:
            return jsonify({"error": "Text is required"}), 400
        audio_buffer = generate_speech_audio(text)
        return send_file(
            audio_buffer,
            mimetype="audio/wav",
            as_attachment=False,
            download_name="speech.wav",
        )
    except Exception as e:
        print("TTS Error:", e)
        return jsonify({"error": str(e)}), 500


@agent_bp.route("/agent/ask", methods=["POST"])
def ask_agent():
    try:
        data = request.json
        query = data.get("query", "")
        if not query:
            return jsonify({"error": "Query is required"}), 400
        response_text = AgentService.handle_query(query)
        return jsonify({"response": response_text})
    except Exception as e:
        if str(e) == "Text is required":
            return jsonify({})
        return jsonify({"error": str(e)}), 500
