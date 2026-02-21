from flask import Blueprint, jsonify
from config import Config
from services.train_scrape_service import fetch_timetables
from cache import cache

train_bp = Blueprint('train', __name__)


@train_bp.route('/train-schedule', methods=['GET'])
@cache.cached(timeout=60 * 60 * 5)
def get_train_schedule():
    res = fetch_timetables(Config.TRAIN_STATION_URL)
    return jsonify(res)
