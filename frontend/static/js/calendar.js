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

    // Mapping of calendar IDs to colors
    const calendarColors = {
        0: '#FF5733',
        3: '#33FF57',
        4: '#FF3357',
        5: '#3357FF',
        6: '#573357',
        'holiday': 'rgb(93, 181, 197)'
        
        // Add more mappings as needed
    };
    
    function displayCalendarEvents(events) {
        loginButton.style.display = 'none';
        calendarEventsList.innerHTML = '';
        events.forEach(event => {
            const li = document.createElement('li');
            li.className = 'calendar-event';
    
            // Apply color based on the calendar ID
            if (event.organizer && event.organizer.email) {
                const calendarId = event.organizer.email.split('@')[0];
                color = '#f9f9f9'
                if (calendarId.split('#')[1] == 'holiday') {
                    color = calendarColors['holiday']
                } else if (calendarColors[event.calendar_index]) {
                    color = calendarColors[event.calendar_index]
                }
                li.style.backgroundColor = color;
            }
    
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
    fetchCalendarEvents();
    setInterval(fetchCalendarEvents, 300000); // Refresh every 5 minutes (300000 milliseconds)
});