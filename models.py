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
    hourly_precipitation = TextField()
    first_time = DateTimeField()
    # condition = CharField()
    last_updated = DateTimeField(default=datetime.datetime.now)

class ShoppingListItem(BaseModel):
    item_name = CharField()
    quantity = IntegerField(default=1)
    purchased = BooleanField(default=False)

class SmartHomeDevice(BaseModel):
    name = CharField()
    activated = BooleanField(default=True)
    mode = CharField(null=True)

# Create tables if they don't exist
database.connect()
# database.drop_tables([SmartHomeDevice])
database.create_tables([Task, WeatherData, ShoppingListItem, SmartHomeDevice])