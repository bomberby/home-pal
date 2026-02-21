from flask import Blueprint, jsonify, request
from playhouse.shortcuts import model_to_dict
from models import ShoppingListItem

shopping_bp = Blueprint('shopping', __name__)


@shopping_bp.route('/shopping-list-items', methods=['GET'])
def get_shopping_list_items():
    items = [model_to_dict(item) for item in ShoppingListItem.select()]
    return jsonify(items)


@shopping_bp.route('/shopping-list-items', methods=['POST'])
def add_shopping_list_item():
    data = request.json
    item = ShoppingListItem.create(
        item_name=data['item_name'],
        quantity=data.get('quantity', 1),
        purchased=data.get('purchased', False)
    )
    return jsonify(model_to_dict(item))


@shopping_bp.route('/shopping-list-items/<int:item_id>', methods=['PUT'])
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


@shopping_bp.route('/shopping-list-items/<int:item_id>', methods=['DELETE'])
def delete_shopping_list_item(item_id):
    try:
        item = ShoppingListItem.get(ShoppingListItem.id == item_id)
        item.delete_instance()
        return jsonify({'message': 'Item deleted successfully'}), 200
    except ShoppingListItem.DoesNotExist:
        return jsonify({'error': 'Item not found'}), 404
