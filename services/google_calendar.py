from google_auth_oauthlib.flow import Flow
from flask import Blueprint, request, session, redirect, url_for, jsonify
from google.auth.credentials import Credentials
import os
import pickle
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import requests
from cache import cache

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
REDIRECT_URI = f'http://127.0.0.1:5000/oauth/oauth2callback'

# Initialize the Flow object
flow = Flow.from_client_secrets_file(
    'env\secrets\client_secret.json',
    scopes=SCOPES,
    redirect_uri=REDIRECT_URI
)
google_calendar = Blueprint('google_calendar', __name__, template_folder='templates')

def credentials_from_storage():
    if not os.path.exists('token.pickle'):
        raise FileNotFoundError("Google Calendar not authorised — visit /oauth/login to connect.")
    return pickle.load(open('token.pickle', 'rb'))

@google_calendar.route('/oauth/login')
def login():
    authorization_url, state = flow.authorization_url(access_type='offline')
    session['state'] = state
    return redirect(authorization_url)

@google_calendar.route('/oauth/oauth2callback')
def oauth2callback():
    if request.args.get('state') != session.get('state'):
        return 'Invalid state parameter', 401

    flow.fetch_token(authorization_response=request.url)
    credentials = flow.credentials

    # Save the credentials for future use
    with open('token.pickle', 'wb') as token:
        pickle.dump(credentials, token)

    return redirect(url_for('index'))

@google_calendar.route('/oauth/logout')
def logout():
    credentials = credentials_from_storage()
    requests.post('https://oauth2.googleapis.com/revoke',
        params={'token': credentials.token},
        headers = {'content-type': 'application/x-www-form-urlencoded'})
    return redirect(url_for('index'))

@cache.memoize(timeout=60 * 60)  # Cache the result for 1 hour
def get_all_events():
    # Load credentials from the session if available, otherwise load from file
    credentials = credentials_from_storage()
    
    service = build('calendar', 'v3', credentials=credentials)

    # Call the Calendar API with time range filter
    today = datetime.utcnow().date()
    one_year_from_now = today + timedelta(days=362)
    
    # Fetch a list of all calendars
    calendar_list = service.calendarList().list().execute()
    calendars = calendar_list.get('items', [])
    
    all_events = []
    calendar_indices = {calendar['id']: index for index, calendar in enumerate(calendars)}
    
    for calendar in calendars:
        events_result = service.events().list(calendarId=calendar['id'], maxResults=10, singleEvents=True,
                                                orderBy='startTime',
                                                timeMin=today.isoformat() + 'T00:00:00Z',
                                                timeMax=one_year_from_now.isoformat() + 'T23:59:59Z').execute()
        events = events_result.get('items', [])
        for event in events:
            event['calendar_index'] = calendar_indices[calendar['id']]
            all_events.append(event)
    

    
    # Sort events by start time
    # Ensure safe access to 'dateTime' and 'date'
    sorted_events = sorted(all_events, key=lambda x: (x['start'].get('dateTime') or x['start'].get('date')))
    
    events = sorted_events
    with open('token.pickle', 'wb') as token:
        pickle.dump(credentials, token)
    return events
    
@google_calendar.route('/calendar/events')
def get_calendar_events():
    events = get_all_events()

    # Filter out events without a summary field
    events = [event for event in events if 'summary' in event]

    # Write the credentials in case the refresh token changed


    return jsonify(events)