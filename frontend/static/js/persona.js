const PERSONA_REFRESH_INTERVAL = 60 * 1000;
const PERSONA_POLL_INTERVAL = 5000;

let personaPollTimer = null;
let _lastImageUrl = null;
let _lastQuote = null;
let _lastSuggestion = null;
let _widgetVersion = null;

async function fetchPersona() {
  try {
    const response = await fetch('/persona');
    const data = await response.json();

    if (data.widget_version) {
      if (_widgetVersion && _widgetVersion !== data.widget_version) {
        location.reload();
        return;
      }
      _widgetVersion = data.widget_version;
    }

    if (data.state === 'absent') {
      hidePersona();
      personaPollTimer = setTimeout(fetchPersona, 30 * 1000); // poll every 30s while away
    } else if (data.generating) {
      if (!_lastImageUrl) showPersonaSpinner();   // only spinner if nothing is showing
      personaPollTimer = setTimeout(fetchPersona, PERSONA_POLL_INTERVAL);
    } else {
      clearTimeout(personaPollTimer);
      showPersona(data.image_url, data.quote, data.suggestion ?? null);
      if (data.stats) renderHud(data.stats);
      if (data.new_unlock) showUnlockToast(data.new_unlock);
    }
  } catch (error) {
    console.error('Error fetching persona:', error);
  }
}

function hidePersona() {
  document.getElementById('persona-spinner').style.display = 'none';
  document.getElementById('persona-content').style.display = 'none';
  document.getElementById('persona-speak-btn').style.display = 'none';
  const suggEl = document.getElementById('persona-suggestion');
  if (suggEl) suggEl.style.display = 'none';
}

function showPersonaSpinner() {
  document.getElementById('persona-content').style.display = 'none';
  document.getElementById('persona-spinner').style.display = 'block';
  document.getElementById('persona-speak-btn').style.display = 'none';
}

function showPersona(imageUrl, quote, suggestion) {
  document.getElementById('persona-spinner').style.display = 'none';
  document.getElementById('persona-content').style.display = 'flex';

  const img = document.getElementById('persona-img');
  const bubble = document.getElementById('persona-bubble');
  const suggEl = document.getElementById('persona-suggestion');

  const sameImage = _lastImageUrl === imageUrl;
  const sameQuote = _lastQuote === quote;
  const sameSuggestion = _lastSuggestion === suggestion;

  if (sameImage && sameQuote && sameSuggestion) return; // nothing changed

  const speakBtn = document.getElementById('persona-speak-btn');

  if (img.naturalWidth > 0) {
    // Already showing something — crossfade only what changed
    if (!sameImage) img.style.opacity = '0';
    if (!sameQuote) bubble.style.opacity = '0';
    if (!sameSuggestion && suggEl) suggEl.style.opacity = '0';
    setTimeout(() => {
      if (!sameQuote) {
        _lastQuote = quote;
        bubble.textContent = quote;
        bubble.style.opacity = '1';
      }
      if (!sameSuggestion && suggEl) {
        _lastSuggestion = suggestion;
        if (suggestion) {
          suggEl.textContent = suggestion;
          suggEl.style.display = '';
          suggEl.style.opacity = '1';
        } else {
          suggEl.style.display = 'none';
        }
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
    _lastSuggestion = suggestion;
    img.src = imageUrl + '?t=' + Date.now();
    bubble.textContent = quote;
    if (suggEl) {
      if (suggestion) {
        suggEl.textContent = suggestion;
        suggEl.style.display = '';
      } else {
        suggEl.style.display = 'none';
      }
    }
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

function renderHud(stats) {
  if (!stats) return;
  const hud = document.getElementById('persona-hud');
  if (!hud) return;
  if (stats.enabled === false) {
    hud.style.display = 'none';
    return;
  }
  if (!stats.level) return;
  hud.style.display = '';
  document.getElementById('hud-level').textContent = `Lv.${stats.level}`;
  const xpPct = stats.xp_needed > 0 ? Math.round((stats.xp_progress / stats.xp_needed) * 100) : 100;
  document.getElementById('hud-xp-bar').style.width = xpPct + '%';
  document.getElementById('hud-xp-text').textContent = `${stats.xp_progress}/${stats.xp_needed}`;
  document.getElementById('hud-aff-bar').style.width = stats.affection + '%';
  document.getElementById('hud-nrg-bar').style.width = stats.energy + '%';
}

function showUnlockToast(moodKey) {
  const toast = document.getElementById('persona-unlock-toast');
  if (!toast) return;
  toast.textContent = `✨ new mood unlocked: ${moodKey}`;
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 3500);
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('persona-speak-btn')?.addEventListener('click', speakQuote);
  fetchPersona();
  setInterval(fetchPersona, PERSONA_REFRESH_INTERVAL);
});
