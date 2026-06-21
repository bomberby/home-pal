from peewee import *
import datetime
import json

# Connect to SQLite database
database = SqliteDatabase('my_database.db')

class BaseModel(Model):
    class Meta:
        database = database

class Task(BaseModel):
    task_name = CharField()
    due_date = DateTimeField(default=datetime.datetime.now)
    completed = BooleanField(default=False)

class WeatherData(BaseModel):
    city = CharField()
    latitude = FloatField()
    longitude = FloatField()
    timezone = CharField()
    hourly_temperatures = TextField()
    hourly_precipitation = TextField()
    hourly_weathercodes = TextField(null=True)
    first_time = DateTimeField()
    last_updated = DateTimeField(default=datetime.datetime.now)

class ShoppingListItem(BaseModel):
    item_name = CharField()
    quantity = IntegerField(default=1)
    purchased = BooleanField(default=False)

class SmartHomeDevice(BaseModel):
    name = CharField()
    activated = BooleanField(default=True)
    mode = CharField(null=True)

class WeatherLocation(BaseModel):
    location_name = CharField(unique=True)
    is_default = BooleanField(default=False)

class AirQualityData(BaseModel):
    city = CharField(unique=True)
    latitude = FloatField()
    longitude = FloatField()
    hourly_aqi = TextField()   # JSON list of European AQI values
    hourly_pm25 = TextField()  # JSON list of PM2.5 µg/m³ values
    hourly_pm10 = TextField()  # JSON list of PM10 µg/m³ values
    first_time = DateTimeField()
    last_updated = DateTimeField(default=datetime.datetime.now)

_DEFAULT_UNLOCKED_MOODS = json.dumps([
    'cheerful', 'content', 'dreamy', 'tired', 'resigned',
    'flustered', 'focused', 'worried', 'annoyed', 'melancholy',
])

class PersonaStats(BaseModel):
    xp = IntegerField(default=0)
    affection = IntegerField(default=0)
    streak_days = IntegerField(default=0)
    last_seen_date = DateField(null=True)
    unlocked_moods = TextField(default=_DEFAULT_UNLOCKED_MOODS)
    last_xp_event_at = DateTimeField(null=True)
    enabled = BooleanField(default=True)

    @classmethod
    def singleton(cls):
        row, _ = cls.get_or_create(id=1, defaults={
            'xp': 0,
            'affection': 0,
            'streak_days': 0,
            'last_seen_date': None,
            'unlocked_moods': _DEFAULT_UNLOCKED_MOODS,
            'last_xp_event_at': None,
            'enabled': True,
        })
        return row

    @classmethod
    def reset(cls):
        cls.delete().execute()
        return cls.singleton()


# Create tables if they don't exist
database.connect()
database.create_tables([Task, WeatherData, ShoppingListItem, SmartHomeDevice, WeatherLocation, AirQualityData, PersonaStats])
