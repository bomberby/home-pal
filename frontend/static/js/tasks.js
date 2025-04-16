// Fetch tasks and update the UI
function fetchTasks() {
  fetch('/tasks')
    .then(response => response.json())
    .then(tasks => {
      const taskList = document.getElementById('tasks-list');
      taskList.innerHTML = '';
      tasks.forEach(task => {
        const li = document.createElement('li');
        li.innerHTML = `<span>${task.task_name} - Due: ${new Date(task.due_date).toLocaleString()}</span>`;
        if (task.completed) {
          li.classList.add('completed');
        }

        // Add delete button
        const deleteButton = document.createElement('button');
        deleteButton.textContent = 'x';
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

// Function to read out incomplete tasks
function readIncompleteTasks() {
  const taskList = document.getElementById('tasks-list');
  const incompleteTasks = Array.from(taskList.children)
    .filter(li => !li.classList.contains('completed'))
    .map(li => li.textContent.split(' - ')[0]);

  if (incompleteTasks.length === 0) {
    alert('No incomplete tasks.');
    return;
  }

  utterance = new SpeechSynthesisUtterance(incompleteTasks.join('. '));
  utterance.voice = speechSynthesis.getVoices()[2];
  speechSynthesis.speak(utterance);
}

const allVoicesObtained = new Promise(function(resolve, reject) {
  let voices = window.speechSynthesis.getVoices();
  if (voices.length !== 0) {
    resolve(voices);
  } else {
    window.speechSynthesis.addEventListener("voiceschanged", function() {
      voices = window.speechSynthesis.getVoices();
      resolve(voices);
    });
  }
});

function initiateTasks() {
    fetchTasks();

    // Add button to trigger text-to-speech
    const readButton = document.createElement('button');
    readButton.textContent = '📢';
    readButton.style.marginTop = '10px';
    readButton.addEventListener('click', readIncompleteTasks);
    document.querySelector('.tasks-card').appendChild(readButton);
}
// Add event listener for enter key on the task input field
document.getElementById('new-task').addEventListener('keydown', function(event) {
    if (event.key === 'Enter') {
        event.preventDefault();
        addTask();
    }
});
window.window.addEventListener("load",initiateTasks);
