const PERSONA_REFRESH_INTERVAL = 10 * 60 * 1000;
const PERSONA_POLL_INTERVAL = 5000;

let personaPollTimer = null;

async function fetchPersona() {
  try {
    const response = await fetch('/persona');
    const data = await response.json();

    if (data.generating) {
      showPersonaSpinner();
      personaPollTimer = setTimeout(fetchPersona, PERSONA_POLL_INTERVAL);
    } else {
      clearTimeout(personaPollTimer);
      showPersona(data.image_url, data.quote);
    }
  } catch (error) {
    console.error('Error fetching persona:', error);
  }
}

function showPersonaSpinner() {
  document.getElementById('persona-content').style.display = 'none';
  document.getElementById('persona-spinner').style.display = 'block';
}

function showPersona(imageUrl, quote) {
  document.getElementById('persona-spinner').style.display = 'none';
  document.getElementById('persona-content').style.display = 'flex';

  const img = document.getElementById('persona-img');
  const bubble = document.getElementById('persona-bubble');
  const newSrc = imageUrl + '?t=' + Date.now();

  if (img.src && img.naturalWidth > 0) {
    // Already showing something — crossfade
    img.style.opacity = '0';
    bubble.style.opacity = '0';
    setTimeout(() => {
      bubble.textContent = quote;
      bubble.style.opacity = '1';
      img.onload = () => { img.style.opacity = '1'; };
      img.src = newSrc;
    }, 300); // wait for fade-out before swapping
  } else {
    // First load — appear directly
    img.src = newSrc;
    bubble.textContent = quote;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  fetchPersona();
  setInterval(fetchPersona, PERSONA_REFRESH_INTERVAL);
});
