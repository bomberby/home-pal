from flask import jsonify, request, render_template, send_file, after_this_request
from playhouse.shortcuts import model_to_dict, dict_to_model
from models import BaseModel, Task, WeatherData, ShoppingListItem
from datetime import datetime
import urllib.parse
import json
from weather_service import get_cached_or_fetch
from smart_home_service import get_device, get_device_status, change_device_status
from config import Config
from train_scrape_service import fetch_timetables
from cache import cache
import os
import io

from image_dither import dither_image, dither_bw_image

from google_calender import google_calendar
from tts_service import generate_speech_audio
from agent_service import AgentService

def init_routes(app):
  # homepage
  @app.route('/')
  def index():
    return render_template('index.html')

  # Get all tasks
  @app.route('/tasks', methods=['GET'])
  def get_tasks():
    tasks = [model_to_dict(task) for task in Task.select() if Task]
    return jsonify(tasks)

  # Add a new task
  @app.route('/tasks', methods=['POST'])
  def add_task():
    data = request.json
    task = Task.create(
      task_name=data['task_name'],
      due_date=datetime.fromisoformat(data.get('due_date', datetime.now().isoformat())),
      completed=data.get('completed', False)
    )

    return jsonify(model_to_dict(task))

  # Mark a task as done or undone
  @app.route('/tasks/<int:task_id>/mark_done', methods=['POST'])
  def mark_task_as_done(task_id):
    try:
      task = Task.get(Task.id == task_id)
      data = request.json
      task.completed = data.get('completed', not task.completed)
      task.save()
      return jsonify(model_to_dict(task)), 200
    except Task.DoesNotExist:
      return jsonify({'error': 'Task not found'}), 404

  @app.route('/tasks/<int:task_id>', methods=['DELETE'])
  def delete_task(task_id):
    try:
      task = Task.get(Task.id == task_id)
      task.delete_instance()
      return jsonify({'message': 'Task deleted successfully'}), 200
    except Task.DoesNotExist:
      return jsonify({'error': 'Task not found'}), 404

  @app.route('/shopping-list-items', methods=['POST'])
  def add_shopping_list_item():
    data = request.json
    item = ShoppingListItem.create(
      item_name=data['item_name'],
      quantity=data.get('quantity', 1),
      purchased=data.get('purchased', False)
    )
    return jsonify(model_to_dict(item))

  @app.route('/shopping-list-items', methods=['GET'])
  def get_shopping_list_items():
    items = [model_to_dict(item) for item in ShoppingListItem.select()]
    return jsonify(items)

  @app.route('/shopping-list-items/<int:item_id>', methods=['PUT'])
  def update_shopping_list_item(item_id):
    try:
      item = ShoppingListItem.get(ShoppingListItem.id == item_id)
      data = request.json
      item.item_name = data.get('item_name', item.item_name)
      item.quantity = data.get('quantity', item.quantity)
      item.purchased = data.get('purchased', item.purchased)
      item.save()
      return jsonify(model_to_dict(item))
    except ShoppingListItem.DoesNotExist:
      return jsonify({'error': 'Item not found'}), 404

  @app.route('/shopping-list-items/<int:item_id>', methods=['DELETE'])
  def delete_shopping_list_item(item_id):
    try:
      item = ShoppingListItem.get(ShoppingListItem.id == item_id)
      item.delete_instance()
      return jsonify({'message': 'Item deleted successfully'}), 200
    except ShoppingListItem.DoesNotExist:
      return jsonify({'error': 'Item not found'}), 404

  @app.route('/weather', methods=['GET'])
  def get_weather():
    try:
      locations = request.cookies.get('weather_locations')
      if locations:
        locations = json.loads(urllib.parse.unquote(locations))
      else:
        locations = [Config.WEATHER_LOCATION]

      weather_data_list = get_cached_or_fetch(locations)
      return jsonify(weather_data_list)
    except WeatherData.DoesNotExist:
      return jsonify({'error': 'Task not found'}), 404

  @app.route('/train-schedule', methods=['GET'])
  @cache.cached(timeout=60 * 60 * 5)  # Cache the result for 5 hours
  def get_train_schedule():
    base_url = Config.TRAIN_STATION_URL
    res = fetch_timetables(base_url)
    return jsonify(res)

  @app.route('/tts/speak', methods=['POST'])
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
    
  @app.route("/agent/ask", methods=["POST"])
  def ask_agent():
    try:
      data = request.json
      query = data.get("query", "")

      if not query:
        return jsonify({"error": "Query is required"}), 400

      response_text = AgentService.handle_query(query)

      return jsonify({"response": response_text})

    except Exception as e:
      if (str(e) == "Text is required"):
          return jsonify({})
      return jsonify({"error": str(e)}), 500


  @app.route("/sh/<device_name>", methods=["GET"])
  def device_status(device_name):
    device = get_device_status(device_name)
    device = model_to_dict(device) if isinstance(device, BaseModel) else device

    return jsonify(device)
  @app.route("/sh/<device_name>", methods=["POST"])
  def device_status_post(device_name):
    print(request.get_json())
    device = get_device_status(device_name)
    activated = request.json.get('activated')
    if activated is not None:
      device = change_device_status(device_name, bool(activated))
    device = model_to_dict(device) if isinstance(device, BaseModel) else device
    

    return jsonify(device)

  @app.route("/sh/<device_name>/on", methods=["GET"])
  def device_turn_on(device_name):
    device = change_device_status(device_name, True)
    device = model_to_dict(device) if isinstance(device, BaseModel) else device

    return jsonify(device)
  @app.route("/sh/<device_name>/off", methods=["GET"])
  def device_turn_off(device_name):
    device = change_device_status(device_name, False)
    return jsonify(model_to_dict(device))
  @app.route("/sh/<device_name>/toggle", methods=["GET"])
  def device_toggle(device_name):
    device = get_device(device_name)
    device = change_device_status(device_name, not device.activated)
    return jsonify(model_to_dict(device))
  @app.route("/sh/<device_name>/mode/<mode>", methods=["GET"])
  def device_change_mode(device_name, mode):
    device = get_device(device_name)
    device.mode = mode
    device.save()
    return jsonify(model_to_dict(device))
  @app.route("/sh/<device_name>/mode/<mode>/toggle", methods=["GET"])
  def device_toggle_mode(device_name, mode):
    device = get_device(device_name)
    if (device.activated == False):
      device.activated = True
      device.mode = None
    if (device.mode == mode):
      device.mode = None
    else:
      device.mode = mode
    device.save()
    return jsonify(model_to_dict(device))
  

  @app.route('/image.bin')
  def get_image():
    # black_and_white = False
    black_and_white = True
    if (black_and_white):
      img_io = dither_bw_image()
      return send_file(img_io, mimetype='image/bmp')
    else:
      quantized = dither_image()

      # Compress the image into half-bytes
      pixels = list(quantized.getdata()) # This is a list of 384,000 integers (0-6)

      # Pack two 4-bit pixels into one 8-bit byte
      packed_bytes = bytearray()
      for i in range(0, len(pixels), 2):
          # Pixel 1 goes in the high 4 bits, Pixel 2 in the low 4 bits
          # Formula: (P1 << 4) | P2
          byte_val = ((pixels[i] & 0x0F) << 4) | (pixels[i+1] & 0x0F)
          packed_bytes.append(byte_val)
          
      # return packed_bytes # Total size: 192,000 bytes
      return send_file(io.BytesIO(packed_bytes), mimetype='application/octet-stream')


  app.register_blueprint(google_calendar)
