from flask import jsonify, request, render_template
from playhouse.shortcuts import model_to_dict, dict_to_model
from models import Task, WeatherData, TrainSchedule, ShoppingListItem
from datetime import datetime

from weather_service import get_cached_or_fetch
from config import Config

from google_calender import google_calendar

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
            location = Config.WEATHER_LOCATION
            weather_data = get_cached_or_fetch(location)
            return jsonify(model_to_dict(weather_data))
        except WeatherData.DoesNotExist:
            return jsonify({'error': 'Task not found'}), 404
        
    @app.route('/train-schedule', methods=['GET'])
    def get_train_schedule():
        location = Config.WEATHER_LOCATION
        try:
            train_data = TrainSchedule.get(TrainSchedule.train_id == 'TBD')
            return jsonify(model_to_dict(train_data))
        except TrainSchedule.DoesNotExist:
    
    app.register_blueprint(google_calendar)
