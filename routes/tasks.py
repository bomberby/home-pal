from flask import Blueprint, jsonify, request
from playhouse.shortcuts import model_to_dict
from models import Task
from datetime import datetime

tasks_bp = Blueprint('tasks', __name__)


@tasks_bp.route('/tasks', methods=['GET'])
def get_tasks():
    tasks = [model_to_dict(task) for task in Task.select() if Task]
    return jsonify(tasks)


@tasks_bp.route('/tasks', methods=['POST'])
def add_task():
    data = request.json
    task = Task.create(
        task_name=data['task_name'],
        due_date=datetime.fromisoformat(data.get('due_date', datetime.now().isoformat())),
        completed=data.get('completed', False)
    )
    return jsonify(model_to_dict(task))


@tasks_bp.route('/tasks/<int:task_id>/mark_done', methods=['POST'])
def mark_task_as_done(task_id):
    try:
        task = Task.get(Task.id == task_id)
        data = request.json
        task.completed = data.get('completed', not task.completed)
        task.save()
        return jsonify(model_to_dict(task)), 200
    except Task.DoesNotExist:
        return jsonify({'error': 'Task not found'}), 404


@tasks_bp.route('/tasks/<int:task_id>', methods=['DELETE'])
def delete_task(task_id):
    try:
        task = Task.get(Task.id == task_id)
        task.delete_instance()
        return jsonify({'message': 'Task deleted successfully'}), 200
    except Task.DoesNotExist:
        return jsonify({'error': 'Task not found'}), 404
