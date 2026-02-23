const CAM_REFRESH_MS = 30000;

function formatAge(seconds) {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${Math.round(seconds / 3600)}h`;
}

function createCamCard(deviceId) {
  const card = document.createElement('div');
  card.className = 'card cam-card';
  card.id = `cam-card-${deviceId}`;
  card.innerHTML = `
    <h2>${deviceId}</h2>
    <div class="cam-img-wrapper">
      <img class="cam-img" alt="${deviceId}">
    </div>
    <div class="cam-timestamp"></div>
  `;
  return card;
}

function updateCamCard(deviceId, info) {
  const existing = document.getElementById(`cam-card-${deviceId}`);

  if (info.stale) {
    existing?.remove();
    return;
  }

  let card = existing;
  if (!card) {
    card = createCamCard(deviceId);
    document.getElementById('cam-section').appendChild(card);
  }

  const img = card.querySelector('.cam-img');
  const timestamp = card.querySelector('.cam-timestamp');
  const newSrc = `/cam/${deviceId}/image?t=${info.last_updated_seconds_ago}`;
  if (img.dataset.src !== newSrc) {
    img.dataset.src = newSrc;
    img.src = newSrc;
  }
  timestamp.textContent = `Updated ${formatAge(info.last_updated_seconds_ago)} ago`;
}

async function refreshCams() {
  try {
    const res = await fetch('/cam/');
    const devices = await res.json();
    for (const [deviceId, info] of Object.entries(devices)) {
      updateCamCard(deviceId, info);
    }
  } catch (e) {
    console.error('[Cam] fetch error:', e);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  refreshCams();
  setInterval(refreshCams, CAM_REFRESH_MS);
});
