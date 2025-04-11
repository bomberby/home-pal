// calendar.js

document.addEventListener('DOMContentLoaded', () => {
    const loginButton = document.getElementById('login-button');
    const logoutButton = document.getElementById('logout-button');
    const calendarEventsList = document.getElementById('calendar-events');

    loginButton.addEventListener('click', handleLogin);
    logoutButton.addEventListener('click', handleLogout);

    function handleLogin() {
        window.location.href = '/oauth/login';
    }
    
    function handleLogout() {
        window.location.href = '/oauth/logout';
    }

    async function fetchCalendarEvents() {
        try {
            const response = await fetch('/calendar/events');
            if (response.ok) {
                const events = await response.json();
                displayCalendarEvents(events);
            } else {
                console.error('Failed to fetch calendar events:', response.statusText);
            }
        } catch (error) {
            console.error('Error fetching calendar events:', error);
        }
    }

    function displayCalendarEvents(events) {
        loginButton.style.display = 'none';
        calendarEventsList.innerHTML = '';
        events.forEach(event => {
            const li = document.createElement('li');
            li.className = 'calendar-event';
            if (event.start.dateTime) {
                const startDate = new Date(event.start.dateTime);
                const endDate = new Date(event.end.dateTime);
                li.textContent = `${event.summary} - ${startDate.toLocaleDateString()} ${startDate.toLocaleTimeString()} - ${endDate.toLocaleDateString()} ${endDate.toLocaleTimeString()}`;
            } else {
                const startDate = new Date(event.start.date);
                endDate = new Date(event.end.date);
                endDate.setDate(endDate.getDate() - 1)
                if (startDate.getDate() === endDate.getDate()){
                    li.textContent = `${event.summary} - ${startDate.toLocaleDateString()}`;
                } else {
                    li.textContent = `${event.summary} - ${startDate.toLocaleDateString()} - ${endDate.toLocaleDateString()}`;
                }
            }

            calendarEventsList.appendChild(li);
        });
    }

    // Fetch and display calendar events when the page loads
    fetchCalendarEvents();
});