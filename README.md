# Home Pal
  This repo's purpose is to create a home dashboard with multiple features and information useful for home managment, useful for display on a tablet

  It is an attempt to see what AI code generator can do with local models and testing it's capabilities

# Functions to add:
- Recycling days
- add ability to set task due, and only display tasks that are due
- implement the train schedule
- recuring shopping list
- weather location be changable
- add tests
- on the weather day separator, add date



# Setting up google calender:
"Please follow these steps to set up Google Calendar API credentials:
1. Go to https://console.developers.google.com/
2. Create a new project or select an existing one.
3. Navigate to 'APIs & Services' > 'Credentials'.
4. Click on 'Create Credentials' and select 'OAuth client ID'.
5. Configure the consent screen if prompted.
6. Set the application type to 'Web application', add authorized redirect URIs (e.g., http://127.0.0.1:5000/oauth2callback), and create the credentials.
7. Download the JSON file containing your client ID and secret.
8. Place this JSON file in your project directory and put it in `env/secrets/client_secret.json`
9. Add the users to the test users list.
10. Enable Google Calendar API
