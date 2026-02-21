async function fetchWeather() {
  try {
    const response = await fetch('/weather');
    const weatherData = await response.json();

    // Reset canvas element before drawing new chart
    document.getElementById('weather-chart').outerHTML = '<canvas id="weather-chart" height="300"></canvas>';


    // Parse and prepare data for chart
    const ctx = document.getElementById('weather-chart').getContext('2d');
    const { labels, datasets, firstTime, lastUpdated } = prepareChartData(weatherData, ctx);
   
    // Prepare vertical lines and now marker annotations
    const annotations = prepareAnnotations(labels, firstTime);

    // Render the chart
    renderWeatherChart(ctx, labels, datasets, annotations);

    // Scroll to current time roughly
    const nowIndex = (Date.now() - firstTime) / (3600 * 1000);
    const chartContainer = document.querySelector('.weather-card .container');
    chartContainer.scrollLeft = nowIndex * 12;

    // Update timestamp UI
    document.querySelector('.weather-card .updated').innerText = "Updated at " + (new Date(lastUpdated)).toISOString();

  } catch (error) {
    console.error('Error fetching weather:', error);
  }
}

function prepareChartData(weatherData, ctx) {
  let labels = [];
  const predefinedColors = [
    'rgba(255, 99, 132, 1)',   // red
    'rgba(54, 162, 235, 1)',   // blue
    'rgba(255, 206, 86, 1)',   // yellow
    'rgba(75, 192, 192, 1)',   // teal
    'rgba(153, 102, 255, 1)',  // purple
    'rgba(255, 159, 64, 1)'    // orange
  ];

  let datasets = [];
  let firstTime = null;
  let lastUpdated = null;
  let index = 0;

  for (const [location, weather] of Object.entries(weatherData)) {
    const hourlyTemperatures = JSON.parse(weather.hourly_temperatures);
    const hourlyPrecipitation = JSON.parse(weather.hourly_precipitation);

    if (!firstTime) firstTime = Date.parse(weather.first_time);
    lastUpdated = weather.last_updated;

    // Create time labels for the first location only
    if (labels.length === 0) {
      labels = Array.from({ length: hourlyTemperatures.length }, (_, i) => {
        const time = new Date(firstTime + i * 3600 * 1000);
        return time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
      });
    }

    const color = predefinedColors[index % predefinedColors.length];

    // Temperature line dataset with gradient fill
    datasets.push({
      label: `Temperature (${location}) (°C)`,
      data: hourlyTemperatures,
      borderColor: color,
      backgroundColor: getGradient(ctx, color),  // gradient fill
      fill: true,
      type: 'line',
      yAxisID: 'y',
      tension: 0.4,         // smooth curves
      pointRadius: 3,
      pointHoverRadius: 7,
      pointBackgroundColor: color,
      borderWidth: 3,
    });

    // Precipitation bar dataset
    datasets.push({
      label: `Precipitation (${location}) (mm)`,
      data: hourlyPrecipitation,
      backgroundColor: color,
      type: 'bar',
      yAxisID: 'y2',
      barPercentage: 5.0,
      categoryPercentage: 0.5
    });

    index++;
  }

  return { labels, datasets, firstTime, lastUpdated };
}

// Helper to create vertical gradient for temperature fill
function getGradient(ctx, color) {
  const gradient = ctx.createLinearGradient(0, 0, 0, 300);
  const baseColor = color.replace(/rgba?\(([^)]+)\)/, '255, 99, 132'); // fallback to pinkish red if invalid
  gradient.addColorStop(0, color.replace(/1\)$/, '0.4)'));  // semi-transparent top
  gradient.addColorStop(1, color.replace(/1\)$/, '0)'));    // fully transparent bottom
  return gradient;
}

function prepareAnnotations(labels, firstTime) {
  const annotations = labels.map((label, index) => {
    const time = new Date(firstTime + index * 3600 * 1000);
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
        backgroundColor: 'rgba(207, 207, 207, 0.36)',
        color: 'rgba(56, 55, 55, 0.36)',
        content: time.toLocaleDateString([], { year: 'numeric', month: '2-digit', day: '2-digit' })
      }
    };
  }).filter(annotation => annotation.borderColor !== 'transparent');

  // Add a red vertical line to indicate current time
  const nowIndex = (Date.now() - firstTime) / (3600 * 1000);
  annotations.push({
    type: 'line',
    mode: 'vertical',
    scaleID: 'x',
    value: nowIndex,
    borderColor: 'rgba(255, 0, 0, 0.36)',
    borderWidth: 3
  });

  return annotations;
}

function renderWeatherChart(ctx, labels, datasets, annotations) {
  new Chart(ctx, {
    data: {
      labels: labels,
      datasets: datasets
    },
    options: {
      responsive: false,
      maintainAspectRatio: false,
      scales: {
        x: {
          grid: {
            color: '#e4e8f7',
            borderColor: 'transparent',
            lineWidth: 1
          },
          ticks: {
            color: '#9ca3af',
            maxRotation: 0,
            autoSkip: true
          }
        },
        y: {
          beginAtZero: false,
          grid: {
            color: '#e4e8f7',
            borderColor: 'transparent',
            lineWidth: 1
          },
          ticks: {
            color: '#9ca3af',
            stepSize: 2
          }
        },
        y2: {
          display: true,
          position: 'right',
          grid: {
            drawOnChartArea: false,
          },
          ticks: {
            color: '#9ca3af',
            stepSize: 1,
            beginAtZero: true
          }
        }
      },
      plugins: {
        annotation: {
          annotations: annotations
        },
        legend: {
          display: false
        },
        tooltip: {
          enabled: true,
          backgroundColor: 'rgba(30, 30, 60, 0.85)',
          titleFont: { size: 16, weight: 'bold' },
          bodyFont: { size: 14 },
          cornerRadius: 8,
          padding: 10,
          shadowOffsetX: 0,
          shadowOffsetY: 2,
          shadowBlur: 8,
          shadowColor: 'rgba(0,0,0,0.25)'
        }
      },
      interaction: {
        mode: 'nearest',
        intersect: false
      }
    }
  });
}






function initiateWeather() {
  fetchWeather();
  renderLocationsInModal();
  setInterval(fetchWeather, 300000); // Refresh every 5 minutes (300000 milliseconds)
}

// window.addEventListener("load", initiateWeather);
document.addEventListener('DOMContentLoaded', () => {
  initiateWeather();
});

// Open and close weather settings modal
function openWeatherSettings() {
  document.getElementById('weather-settings-modal').style.display = 'block';
}

function closeWeatherSettings() {
  document.getElementById('weather-settings-modal').style.display = 'none';
}

async function renderLocationsInModal() {
  const response = await fetch('/weather-locations');
  const locations = await response.json();

  const locationsContainer = document.getElementById('weather-location-list');
  locationsContainer.innerHTML = '';
  locations.forEach(({ location_name, is_default }) => {
    const li = document.createElement('li');
    li.textContent = location_name;

    const starButton = document.createElement('button');
    starButton.textContent = is_default ? '★' : '☆';
    starButton.style.marginLeft = '10px';
    starButton.addEventListener('click', () => setDefaultLocation(location_name));
    li.appendChild(starButton);

    // Add delete button
    const deleteButton = document.createElement('button');
    deleteButton.textContent = 'X';
    deleteButton.style.marginLeft = '5px';
    deleteButton.addEventListener('click', () => deleteLocation(location_name));
    li.appendChild(deleteButton);

    locationsContainer.appendChild(li);
  });
}

async function setDefaultLocation(location) {
  await fetch(`/weather-locations/${encodeURIComponent(location)}/set-default`, { method: 'POST' });
  renderLocationsInModal();
}

// Update weather location
async function updateWeatherLocation() {
  const location = document.getElementById('weather-location').value;
  if (location) {
    await fetch('/weather-locations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ location_name: location })
    });
    document.getElementById('weather-location').value = '';
    renderLocationsInModal();
    fetchWeather();
    closeWeatherSettings();
  }
}

async function deleteLocation(location) {
  await fetch(`/weather-locations/${encodeURIComponent(location)}`, { method: 'DELETE' });
  renderLocationsInModal();
  fetchWeather();
}