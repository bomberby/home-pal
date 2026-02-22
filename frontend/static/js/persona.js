const PERSONA_REFRESH_INTERVAL = 60 * 1000;
const PERSONA_POLL_INTERVAL = 5000;

let personaPollTimer = null;
let _lastImageUrl = null;
let _lastQuote = null;

async function fetchPersona() {
  try {
    const response = await fetch('/persona');
    const data = await response.json();

    if (data.state === 'absent') {
      hidePersona();
      personaPollTimer = setTimeout(fetchPersona, 30 * 1000); // poll every 30s while away
    } else if (data.generating) {
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

function hidePersona() {
  document.getElementById('persona-spinner').style.display = 'none';
  document.getElementById('persona-content').style.display = 'none';
  document.getElementById('persona-speak-btn').style.display = 'none';
}

function showPersonaSpinner() {
  document.getElementById('persona-content').style.display = 'none';
  document.getElementById('persona-spinner').style.display = 'block';
  document.getElementById('persona-speak-btn').style.display = 'none';
}

function showPersona(imageUrl, quote) {
  document.getElementById('persona-spinner').style.display = 'none';
  document.getElementById('persona-content').style.display = 'flex';

  const img = document.getElementById('persona-img');
  const bubble = document.getElementById('persona-bubble');

  const sameImage = _lastImageUrl === imageUrl;
  const sameQuote = _lastQuote === quote;

  if (sameImage && sameQuote) return; // nothing changed — skip entirely

  const speakBtn = document.getElementById('persona-speak-btn');

  if (img.naturalWidth > 0) {
    // Already showing something — crossfade only what changed
    if (!sameImage) img.style.opacity = '0';
    if (!sameQuote) bubble.style.opacity = '0';
    setTimeout(() => {
      if (!sameQuote) {
        _lastQuote = quote;
        bubble.textContent = quote;
        bubble.style.opacity = '1';
      }
      if (!sameImage) {
        _lastImageUrl = imageUrl;
        img.onload = () => { img.style.opacity = '1'; };
        img.src = imageUrl + '?t=' + Date.now();
      }
    }, 300);
  } else {
    // First load — appear directly
    _lastImageUrl = imageUrl;
    _lastQuote = quote;
    img.src = imageUrl + '?t=' + Date.now();
    bubble.textContent = quote;
  }

  if (speakBtn) speakBtn.style.display = '';
}

async function speakQuote() {
  if (!_lastQuote) return;
  const btn = document.getElementById('persona-speak-btn');
  btn.disabled = true;
  try {
    const res = await fetch('/tts/speak', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: _lastQuote }),
    });
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    audio.onended = () => { URL.revokeObjectURL(url); btn.disabled = false; };
    audio.onerror = () => { btn.disabled = false; };
    audio.play();
  } catch (e) {
    console.error('TTS error:', e);
    btn.disabled = false;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('persona-speak-btn')?.addEventListener('click', speakQuote);
  fetchPersona();
  setInterval(fetchPersona, PERSONA_REFRESH_INTERVAL);
});
