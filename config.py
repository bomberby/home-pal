import os

class Config:
    SQLALCHEMY_DATABASE_URI = 'sqlite:///site.db'
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'your-secret-key-here'
    DEBUG = True
    JSON_AS_ASCII = False
    WEATHER_LOCATION = 'Tokyo'