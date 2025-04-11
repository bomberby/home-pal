import os

class Config:
    SQLALCHEMY_DATABASE_URI = 'sqlite:///site.db'
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'your-secret-key-here'
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1' # Allow local network oauth for calender
    DEBUG = True
    JSON_AS_ASCII = False
    WEATHER_LOCATION = 'Tokyo'