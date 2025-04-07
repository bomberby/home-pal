from peewee import *
import datetime

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
    first_time = DateTimeField()
    # temperature = FloatField()
    # condition = CharField()
    last_updated = DateTimeField(default=datetime.datetime.now)

class TrainSchedule(BaseModel):
    train_id = CharField()
    destination = CharField()
    departure_time = DateTimeField()
    status = CharField()

# Create tables if they don't exist
database.connect()
# database.drop_tables([WeatherData])
database.create_tables([Task, WeatherData, TrainSchedule])