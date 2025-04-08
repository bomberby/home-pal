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


function initiateTrainSchedule() {
  fetchTrainSchedule();
}

window.window.addEventListener("load",initiateTrainSchedule);
