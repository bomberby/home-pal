// Fetch train schedule
function fetchTrainSchedule() {
  fetch('/train-schedule')
    .then(response => response.json())
    .then(schedule => {
      const scheduleTable = document.getElementById('train-schedule');
      scheduleTable.innerHTML = '';
      const scheduleHeaderTable = document.getElementById('train-schedule-headers');
      scheduleHeaderTable.innerHTML = '';
      const now = Date.now();
      const datePrefix = new Date().toISOString().substring(0, 11);
      scrollToElement = null
      schedule.forEach(direction => {
        const table = document.createElement('table');
        table.className = 'direction-table';

        // Create header row
        const headerRow = table.insertRow();
        const directionCell = headerRow.insertCell(0);
        directionCell.colSpan = 2;
        directionCell.textContent = direction.direction;
        scheduleHeaderTable.appendChild(table);
      });

      schedule.forEach(direction => {
        const table = document.createElement('table');
        table.className = 'direction-table';

        // Create time and train rows
        direction.timetable.forEach(train => {
          const row = table.insertRow();
          const timeCell = row.insertCell(0);
          if (scrollToElement === null && Date.parse(datePrefix + train.time) + 60*60*1000 > now) { // one hours ago
            scrollToElement = row
          } 

          if (train.train) {
            timeCell.textContent = train.time;
            trainCell = row.insertCell(1);
            trainCell.textContent = train.train;
          } else {
            timeCell.textContent = train.time;
            timeCell.colSpan = 2
          }
        });

        scheduleTable.appendChild(table);
      });
      if (scrollToElement) {
        scheduleTable.scrollTop = scrollToElement.offsetTop
      }
    })
    .catch(error => console.error('Error fetching train schedule:', error));
}

function initiateTrainSchedule() {
  fetchTrainSchedule();
}

window.addEventListener("load", initiateTrainSchedule);
