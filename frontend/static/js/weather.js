// Fetch weather data
function fetchWeather() {
  fetch('/weather')
    .then(response => response.json())
    .then(weatherData => {
      document.getElementById('weather-chart').outerHTML = '<canvas id="weather-chart" height="300"></canvas>';
      const ctx = document.getElementById('weather-chart').getContext('2d');
      first_time = null
      last_updated = null
      labels = []
      
      const predefinedColors = [
        'rgba(255, 99, 132, 1)',
        'rgba(54, 162, 235, 1)',
        'rgba(255, 206, 86, 1)',
        'rgba(75, 192, 192, 1)',
        'rgba(153, 102, 255, 1)',
        'rgba(255, 159, 64, 1)'
      ];
      let index = 0;
      let datasets = [];
      for (const [location, weather] of Object.entries(weatherData)) {
        const hourlyTemperatures = JSON.parse(weather.hourly_temperatures);
        const hourlyPrecipitation = JSON.parse(weather.hourly_precipitation);
        first_time = Date.parse(weather.first_time);
        last_updated = weather.last_updated
        labels.push(Array.from({ length: hourlyTemperatures.length }, (_, i) => {
          const time = new Date(first_time + i * 60 * 60 * 1000);
          return `${time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })}`;
        }));
      
        datasets.push({
          label: `Temperature (${location}) (°C)`,
          data: hourlyTemperatures,
          borderColor: predefinedColors[index % predefinedColors.length],
          fill: false,
          type: 'line',
          yAxisID: 'y'
        });
      
        datasets.push({
          label: `Precipitation (${location}) (mm)`,
          data: hourlyPrecipitation,
          backgroundColor: predefinedColors[index % predefinedColors.length],
          fill: false,
          type: 'bar',
          yAxisID: 'y2'
        });
        index++;
      }

      const now_index = (Date.now() - first_time) / 3600 / 1000;
      annotations = labels[0].map((label, index) => {
        const time = new Date(first_time + index * 60 * 60 * 1000);
        return {
          type: 'line',
          mode: 'vertical',
          scaleID: 'x',
          value: index,
          borderColor: label.includes('00:00') ? 'rgba(175, 150, 150, 0.36)' : 'transparent',
          borderWidth: label.includes('00:00') ? 3 : 0,
          label: {
            display: label.includes('00:00'),
            position: '0%',
            // rotation: 90,
            backgroundColor: 'rgba(207, 207, 207, 0.36)',
            color: 'rgba(56, 55, 55, 0.36)',
            content: time.toLocaleDateString([], { year: 'numeric', month: '2-digit', day: '2-digit' })
          }
        };
      }).filter(annotation => annotation.borderColor !== 'transparent');
      annotations.push({
        type: 'line',
        mode: 'vertical',
        scaleID: 'x',
        value: now_index,
        borderColor: 'rgba(255, 0, 0, 0.36)',
        borderWidth: 3
      });

      const chartContainer = document.querySelector('.weather-card .container');
      chartContainer.scrollLeft = now_index * 12;
      document.querySelector('.weather-card .updated').innerText = "Updated at " + (new Date(last_updated)).toISOString();

      new Chart(ctx, {
        data: {
          labels: labels[0],
          datasets: datasets
        },
        options: {
          responsive: false,
          maintainAspectRatio: false,
          scales: {
            y: {
              beginAtZero: false
            },
            y2: {
              display: false
            }
          },
          plugins: {
            annotation: {
              annotations: annotations
            },
            legend: {
              display: false,
            }
          }
        }
      });
    })
    .catch(error => console.error('Error fetching weather:', error));
}

function initiateWeather() {
  fetchWeather();
  renderLocationsInModal();
  setInterval(fetchWeather, 300000); // Refresh every 5 minutes (300000 milliseconds)
}

window.addEventListener("load", initiateWeather);

// Open and close weather settings modal
function openWeatherSettings() {
  document.getElementById('weather-settings-modal').style.display = 'block';
}

function closeWeatherSettings() {
  document.getElementById('weather-settings-modal').style.display = 'none';
}

function renderLocationsInModal() {
  const locations = JSON.parse(decodeURIComponent(getCookieValue('weather_locations')));
  const locationsContainer = document.getElementById('weather-location-list');
  locationsContainer.innerHTML = '';
  locations.forEach(location => {
    const li = document.createElement('li');
    li.textContent = location;

    // Add delete button
    const deleteButton = document.createElement('button');
    deleteButton.textContent = 'X';
    deleteButton.style.marginLeft = '10px';
    deleteButton.addEventListener('click', () => deleteLocation(location));
    li.appendChild(deleteButton);

    locationsContainer.appendChild(li);
  });
}

// Update weather location
function updateWeatherLocation() {
  const location = document.getElementById('weather-location').value;
  if (location) {
    // Store the location in a cookie
    locationsList = JSON.parse(decodeURIComponent(getCookieValue('weather_locations'))) || [];
    locationsList.push(location);
    document.cookie = `weather_locations=${encodeURIComponent(JSON.stringify(locationsList))}; path=/`;
    renderLocationsInModal();
    closeWeatherSettings();
  }
}

function deleteLocation(location) {
  locationsList = JSON.parse(decodeURIComponent(getCookieValue('weather_locations'))).filter(loc => location !== loc);
  document.cookie = `weather_locations=${encodeURIComponent(JSON.stringify(locationsList))}; path=/`;
  renderLocationsInModal();
}