from routes.main import main_bp
from routes.tasks import tasks_bp
from routes.shopping import shopping_bp
from routes.weather import weather_bp
from routes.train import train_bp
from routes.agent import agent_bp
from routes.smart_home import smart_home_bp
from routes.persona import persona_bp
from routes.eink import eink_bp
from services.google_calendar import google_calendar


def init_routes(app):
    app.register_blueprint(main_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(shopping_bp)
    app.register_blueprint(weather_bp)
    app.register_blueprint(train_bp)
    app.register_blueprint(agent_bp)
    app.register_blueprint(smart_home_bp)
    app.register_blueprint(persona_bp)
    app.register_blueprint(eink_bp)
    app.register_blueprint(google_calendar)
