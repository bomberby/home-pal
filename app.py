from flask import Flask
import config
from models import database, Task, WeatherData, TrainSchedule
from routes import init_routes


def create_app():
    app = Flask(__name__)
    app.config.from_object(config.Config)
    app.template_folder = 'frontend/templates'
    app.static_folder = 'frontend/static'
    
    # Initialize the Peewee ORM with Flask
    def before_request():
        database.connect()

    def after_request(response):
        database.close()
        return response

    app.before_request(before_request)
    app.after_request(after_request)

    # Register routes
    init_routes(app)

    return app

if __name__ == '__main__':
    app = create_app()
    app.run(debug=config.Config.DEBUG)