// Fetch weather data
function fetchWeather() {
  fetch('/weather')
    .then(response => response.json())
    .then(weather => {
      const ctx = document.getElementById('weather-chart').getContext('2d');
      const hourlyTemperatures = JSON.parse(weather.hourly_temperatures);
      const first_time = Date.parse(weather.first_time);
      const labels = Array.from({ length: hourlyTemperatures.length }, (_, i) => {
        const time = new Date(first_time + i * 60 * 60 * 1000);
        return `${time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })}`;
      });
      const now_index = (Date.now() - first_time) / 3600 / 1000;
      annotations = labels.map((label, index) => ({
        type: 'line',
        mode: 'vertical',
        scaleID: 'x',
        value: index,
        borderColor: label && label.includes('00:00') ? 'rgba(175, 150, 150, 0.36)' : 'transparent',
        borderWidth: label && label.includes('00:00') ? 3 : 0
      })).filter(annotation => annotation.borderColor !== 'transparent');
      annotations.push({
        type: 'line',
        mode: 'vertical',
        scaleID: 'x',
        value: now_index,
        borderColor: 'rgba(255, 0, 0, 0.36)',
        borderWidth: 3
      });
      console.log(annotations)
      
      const chartContainer = document.querySelector('.weather-card .container');
      chartContainer.scrollLeft += now_index * 12;
      
      new Chart(ctx, {
        data: {
          labels: labels,
          datasets: [{
            label: 'Temperature (°C)',
            type: 'line',
            data: hourlyTemperatures,
            borderColor: 'rgba(75, 192, 192, 1)',
            fill: false
          }]
        },
        options: {
          responsive: false,
          maintainAspectRatio: false,
          scales: {
            y: {
              beginAtZero: false
            }
          },
          plugins: {
            annotation: {
              annotations: annotations
            }
          }
        }
      });
    })
    .catch(error => console.error('Error fetching weather:', error));
}


function initiateWeather() {
  fetchWeather();
}

window.window.addEventListener("load",initiateWeather);
