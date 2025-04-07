// Fetch tasks and update the UI
function fetchTasks() {
  fetch('/tasks')
    .then(response => response.json())
    .then(tasks => {
      const taskList = document.getElementById('tasks-list');
      taskList.innerHTML = '';
      tasks.forEach(task => {
        const li = document.createElement('li');
        li.textContent = `${task.task_name} - Due: ${new Date(task.due_date).toLocaleString()}`;
        if (task.completed) {
          li.classList.add('completed');
        }

        // Add delete button
        const deleteButton = document.createElement('button');
        deleteButton.textContent = 'Delete';
        deleteButton.style.marginLeft = '10px';
        deleteButton.addEventListener('click', () => deleteTask(task.id, li));
        li.appendChild(deleteButton);

        li.addEventListener('click', () => markTask(task.id, li));
        taskList.appendChild(li);
      });
    })
    .catch(error => console.error('Error fetching tasks:', error));
}

// Add a new task
function addTask() {
  const input = document.getElementById('new-task');
  if (input.value.trim()) {
    const taskData = {
      task_name: input.value.trim(),
      due_date: new Date().toISOString()
    };
    
    fetch('/tasks', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(taskData)
    })
    .then(response => response.json())
    .then(() => {
      input.value = '';
      fetchTasks();
    })
    .catch(error => console.error('Error adding task:', error));
  }
}

// Mark a task as completed or incomplete
function markTask(taskId, li) {
  fetch(`/tasks/${taskId}/mark_done`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ completed: !li.classList.contains('completed') })
  })
  .then(response => response.json())
  .then(data => {
    if (data.id) {
      li.classList.toggle('completed');
    } else {
      console.error('Error marking task:', data.error);
    }
  })
  .catch(error => console.error('Error marking task:', error));
}

// Delete a task
function deleteTask(taskId, li) {
  fetch(`/tasks/${taskId}`, {
    method: 'DELETE',
    headers: {
      'Content-Type': 'application/json'
    }
  })
  .then(response => response.json())
  .then(data => {
    if (data.message) {
      li.remove();
    } else {
      console.error('Error deleting task:', data.error);
    }
  })
  .catch(error => console.error('Error deleting task:', error));
}

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

// Fetch train schedule
function fetchTrainSchedule() {
  fetch('/train-schedule')
    .then(response => response.json())
    .then(schedule => {
      const scheduleList = document.getElementById('train-schedule');
      scheduleList.innerHTML = '';
      schedule.forEach(train => {
        const li = document.createElement('li');
        li.textContent = `${train.train_id} to ${train.destination} at ${new Date(train.departure_time).toLocaleTimeString()}`;
        scheduleList.appendChild(li);
      });
    })
    .catch(error => console.error('Error fetching train schedule:', error));
}

// Initial load
window.onload = function() {
  fetchTasks();
  fetchWeather();
  fetchTrainSchedule();
};
