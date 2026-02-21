import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import datetime

def fetch_timetable(url, timetable_type):
  print(f"Fetching timetable from: {url} for {timetable_type}")
  # Send a GET request to the timetable URL
  response = requests.get(url)

  # Parse the HTML content with BeautifulSoup
  soup = BeautifulSoup(response.content, 'html.parser')

  # Find all rows that contain the timetable data (adjust the parent element)
  rows = soup.find_all('tr')  # Or another tag if rows are under a different element


  departure_times = []
  # Loop through each row and extract the hour and minute
  for row in rows:
      # Find the hour (first element in the row)
      hour = row.find('td')  # This assumes the first td contains the hour
      if hour:
          hour = hour.get_text(strip=True)
      
      # Find all div elements with class 'timetable_time' for the minutes
      timetable_times = row.find_all('div', class_='timetable_time')
      
      # Extract and print the time for each timetable_time element
      for timetable_time in timetable_times:
          minute = timetable_time.find_next(class_="minute").get_text(strip=True)
          train = timetable_time.find_next(class_="train").get_text(strip=True)
          departure_times.append({"time": f"{hour.rjust(2,'0')}:{minute}", "train": train})

  return departure_times



# Function to check if today is a weekend
def is_weekend():
    today = datetime.datetime.today().weekday()
    return today == 5 or today == 6  # 5 is Saturday, 6 is Sunday

def fetch_timetables(base_url):
  # Send a GET request to Station timetable page
  response = requests.get(base_url)
  response.raise_for_status()

  # Parse the page content using BeautifulSoup
  soup = BeautifulSoup(response.content, "html.parser")

  # Debugging line to confirm fetching the Station page
  print("Fetched Station page successfully.")

  # Store timetable links in a dictionary format
  timetable_data = []

  # Find the timetable links for Station
  table = soup.find("table", class_="result_02")

  # Check if the timetable table is found
  if table:
      print("Timetable table found.")
      for row in table.find_all("tr"):
          route_name = row.find("th")
          if route_name and "Line" in route_name.get_text():
              # Find all timetable links for Line
              for link in row.find_all("a", class_="fortimeLink"):
                  timetable_url = urljoin(base_url, link["href"])
                  timetable_type = "Weekday" if "Weekdays" in link.get_text() else "Weekend"
                  direction = row.find_all("td")[0].get_text(strip=True)
                  timetable_data.append({
                      "direction": direction,
                      "type": timetable_type,
                      "url": timetable_url
                  })
      print(f"Found timetable records: {timetable_data}")
  else:
      print("Timetable table not found.")

  # Determine if today is a weekend or weekday
  is_today_weekend = is_weekend()

  timetables = []

  # Based on today’s day, fetch the relevant timetable URL (only weekday or weekend)
  for timetable in timetable_data:
      if (is_today_weekend and timetable["type"] == "Weekend") or (not is_today_weekend and timetable["type"] == "Weekday"):
          print(f"Fetching timetable for {timetable['type']} in direction {timetable['direction']}")
          time_list = fetch_timetable(timetable["url"], timetable["type"])
          timetables.append({"direction": timetable['direction'], "type": timetable["type"], "timetable": time_list})

  return timetables
