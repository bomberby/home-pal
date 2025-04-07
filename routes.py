from flask import jsonify, request, render_template
from playhouse.shortcuts import model_to_dict, dict_to_model
from models import Task, WeatherData, TrainSchedule
from datetime import datetime

from weather_service import get_cached_or_fetch

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
    
    from config import Config
    
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
            return jsonify({'error': 'Weather data not found'}), 404
    # Similar routes for train schedules...
