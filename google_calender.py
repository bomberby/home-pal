from google_auth_oauthlib.flow import Flow
from flask import Blueprint, request, session, redirect, url_for, jsonify
from google.auth.credentials import Credentials
import pickle
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import requests


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


@google_calendar.route('/calendar/events')
def get_calendar_events():
    # Load credentials from the session if available, otherwise load from file
    credentials = credentials_from_storage()
    
    service = build('calendar', 'v3', credentials=credentials)

    # Call the Calendar API with time range filter
    today = datetime.utcnow().date()
    one_year_from_now = today + timedelta(days=365)
    
    events_result = service.events().list(calendarId='primary', maxResults=10, singleEvents=True,
                                        orderBy='startTime',
                                        timeMin=today.isoformat() + 'T00:00:00Z',
                                        timeMax=one_year_from_now.isoformat() + 'T23:59:59Z').execute()
    events = events_result.get('items', [])
    # Write the credentials in case the refresh token changed
    with open('token.pickle', 'wb') as token:
        pickle.dump(credentials, token)

    return jsonify(events)