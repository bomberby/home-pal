// persona_desktop.js — self-contained module for the desktop floating widget.

'use strict';

// ─── Module state ─────────────────────────────────────────────────────────────

const AMBIENT_REFRESH_MS = 60_000;
const POLL_GENERATING_MS =  5_000;
const ABSENT_POLL_MS     = 30_000;

let _lastImageUrl = null;
let _lastQuote    = null;
let _ambientTimer = null;

// ─── Split layout constants ────────────────────────────────────────────────────
const HEADER_H             = 30;
const TOGGLE_H             = 28;
const IMAGE_MIN_H          = 150;   // px — image never shorter than this
const CHAT_MIN_H           = 100;   // px — chat never shorter than this
const IMAGE_MAX_VH_FILL    = 0.65;  // image capped at 65 vh in fill mode
const DEFAULT_SPLIT_RATIO  = 0.58;  // image fraction of available height
const DEFAULT_NATURAL_H    = 550;   // px — threshold below which constrained mode activates
const DRAG_THRESHOLD_PX    = 5;     // px — min movement before mousedown becomes a drag

// ─── Split layout state ────────────────────────────────────────────────────────
let _splitRatio      = DEFAULT_SPLIT_RATIO;
let _naturalTotalH   = DEFAULT_NATURAL_H;
let _fillMode        = true;
let _dragStartY      = null;
let _dragStartImgH   = null;
let _dragging        = false;

// TTS auto-speak — persisted via Python to window_pos.json, default on.
// _applyPersistedTts() is called by the launcher after page load to push the
// saved value in (localStorage is ephemeral in pywebview's WebView2 instance).
let _ttsEnabled = true;

// Ephemeral conversation history [[userMsg, personaReply], ...]
const _history = [];

let _chatOpen          = true;
let _chatMsgCount      = 0;
let _autoCollapseTimer = null;
const AUTO_COLLAPSE_MS = 5 * 60 * 1000;

// ─── Window controls ──────────────────────────────────────────────────────────

function _callApi(method, ...args) {
  if (window.pywebview && window.pywebview.api) {
    window.pywebview.api[method](...args);
  }
}

// ─── TTS toggle ───────────────────────────────────────────────────────────────

function _applyTtsState() {
  const btn = document.getElementById('btn-tts-toggle');
  if (!btn) return;
  if (_ttsEnabled) {
    btn.textContent = '🔊';
    btn.title       = 'Mute auto-speak';
    btn.classList.remove('muted');
  } else {
    btn.textContent = '🔇';
    btn.title       = 'Unmute auto-speak';
    btn.classList.add('muted');
  }
}

function _applyPersistedTts(enabled) {
  _ttsEnabled = !!enabled;
  _applyTtsState();
}

function _toggleTts() {
  _ttsEnabled = !_ttsEnabled;
  _callApi('setTtsEnabled', _ttsEnabled);
  _applyTtsState();
}

// ─── Chat panel collapse ──────────────────────────────────────────────────────

function _applyChatOpenState(open) {
  _chatOpen = open;
  const chevron = document.getElementById('chat-chevron');
  const toggle  = document.getElementById('chat-toggle');
  const badge   = document.getElementById('chat-badge');
  if (open) {
    chevron?.classList.remove('collapsed');
    toggle?.setAttribute('aria-expanded', 'true');
    if (badge) badge.style.display = 'none';
    setTimeout(() => document.getElementById('chat-input')?.focus(), 340);
  } else {
    chevron?.classList.add('collapsed');
    toggle?.setAttribute('aria-expanded', 'false');
    if (badge) {
      if (_chatMsgCount > 0) {
        badge.textContent   = _chatMsgCount > 99 ? '99+' : String(_chatMsgCount);
        badge.style.display = '';
      } else {
        badge.style.display = 'none';
      }
    }
  }
  _applyLayout();
}

function _applyPersistedChatOpen(open) {
  _chatOpen = !!open;
  // Update chevron/badge to match saved state before first _applyLayout runs
  const chevron = document.getElementById('chat-chevron');
  const toggle  = document.getElementById('chat-toggle');
  if (!_chatOpen) {
    chevron?.classList.add('collapsed');
    toggle?.setAttribute('aria-expanded', 'false');
  }
  _applyLayout();
}

function _toggleChat() {
  if (_fillMode) return;   // no collapse in fill mode
  const next = !_chatOpen;
  _applyChatOpenState(next);
  _callApi('setChatOpen', next);
}

function _resetAutoCollapse() {
  clearTimeout(_autoCollapseTimer);
  _autoCollapseTimer = setTimeout(() => {
    if (_fillMode) return;   // no collapse in fill mode
    if (_chatOpen) { _applyChatOpenState(false); _callApi('setChatOpen', false); }
  }, AUTO_COLLAPSE_MS);
}

// ─── Split layout logic ────────────────────────────────────────────────────────

function _availableH() {
  return window.innerHeight - HEADER_H - TOGGLE_H;
}

function _applyLayout() {
  const avail     = _availableH();
  const imgWrap   = document.getElementById('persona-img-wrap');
  const chatPanel = document.getElementById('chat-panel');
  const toggle    = document.getElementById('chat-toggle');
  if (!imgWrap || !chatPanel || !toggle) return;

  if (avail >= _naturalTotalH) {
    // ── Fill mode ──────────────────────────────────────────────────────────────
    _fillMode = true;
    toggle.classList.remove('splitter-active', 'splitter-draggable');
    chatPanel.classList.remove('collapsed');   // chat always visible in fill mode
    // Image: take only what the image naturally needs (no stretching beyond content)
    imgWrap.style.flex      = '0 0 auto';
    imgWrap.style.height    = '';
    imgWrap.style.maxHeight = Math.round(IMAGE_MAX_VH_FILL * window.innerHeight) + 'px';
    // Chat: fill remaining space, unconstrained
    chatPanel.style.flex      = '1 1 auto';
    chatPanel.style.height    = 'auto';
    chatPanel.style.maxHeight = '9999px';
  } else {
    // ── Constrained mode ───────────────────────────────────────────────────────
    _fillMode = false;
    toggle.classList.add('splitter-active');

    if (!_chatOpen) {
      // Collapsed: pointer cursor (click to open), no drag
      toggle.classList.remove('splitter-draggable');
      chatPanel.style.maxHeight = '';
      chatPanel.style.height    = '';
      chatPanel.classList.add('collapsed');
      imgWrap.style.flex      = '1 1 auto';
      imgWrap.style.height    = '';
      imgWrap.style.maxHeight = '';
    } else {
      // Open: ns-resize cursor (drag to resize)
      toggle.classList.add('splitter-draggable');
      chatPanel.classList.remove('collapsed');
      const imageH = Math.round(
        Math.max(IMAGE_MIN_H, Math.min(_splitRatio * avail, avail - CHAT_MIN_H))
      );
      const chatH = avail - imageH;
      imgWrap.style.flex      = `0 0 ${imageH}px`;
      imgWrap.style.height    = '';
      imgWrap.style.maxHeight = '';
      chatPanel.style.flex      = '1 1 auto';
      chatPanel.style.height    = 'auto';
      chatPanel.style.maxHeight = `${chatH}px`;
    }
  }
}

function _applyPersistedSplitRatio(ratio) {
  if (typeof ratio === 'number' && ratio > 0 && ratio < 1) {
    _splitRatio = ratio;
  }
  _applyLayout();
}

// ─── Resize cursor feedback ───────────────────────────────────────────────────
// WebView2 overrides Win32 WM_SETCURSOR, so the resize cursor is injected via
// CSS.  WM_NCCALCSIZE=0 in the launcher makes client area = full window rect,
// so window.innerWidth/Height matches the outer dimensions and the 8px zone
// aligns with the Win32 RESIZE_EDGE used in the NCHITTEST hooks.

const _RESIZE_EDGE_PX = 8;
const _RESIZE_CURSORS = { s: 's-resize', e: 'e-resize', se: 'se-resize' };
let   _resizeStyleEl  = null;

function _setResizeCursor(x, y) {
  const s = y > window.innerHeight - _RESIZE_EDGE_PX;
  const e = x > window.innerWidth  - _RESIZE_EDGE_PX;
  const dir = (s && e) ? 'se' : s ? 's' : e ? 'e' : null;
  if (!_resizeStyleEl) {
    _resizeStyleEl = document.createElement('style');
    document.head.appendChild(_resizeStyleEl);
  }
  _resizeStyleEl.textContent = dir ? `* { cursor: ${_RESIZE_CURSORS[dir]} !important; }` : '';
}

// ─── Ambient persona polling ──────────────────────────────────────────────────

async function fetchAmbient() {
  try {
    const res  = await fetch('/persona');
    const data = await res.json();

    if (data.state === 'absent') {
      _scheduleAmbient(ABSENT_POLL_MS);
      return;
    }
    if (data.generating) {
      _showSpinner();
      _scheduleAmbient(POLL_GENERATING_MS);
      return;
    }
    _updatePersonaDisplay(data.image_url, data.quote);
    _scheduleAmbient(AMBIENT_REFRESH_MS);
  } catch (e) {
    console.error('[persona_desktop] ambient poll error:', e);
    _scheduleAmbient(AMBIENT_REFRESH_MS);
  }
}

function _scheduleAmbient(ms) {
  clearTimeout(_ambientTimer);
  _ambientTimer = setTimeout(fetchAmbient, ms);
}

// ─── Image + bubble display ───────────────────────────────────────────────────

function _showSpinner() {
  document.getElementById('persona-spinner').style.display    = '';
  document.getElementById('persona-img').style.display         = 'none';
  document.getElementById('persona-speak-btn').style.display   = 'none';
}

function _updatePersonaDisplay(imageUrl, quote) {
  const spinner  = document.getElementById('persona-spinner');
  const img      = document.getElementById('persona-img');
  const bubble   = document.getElementById('persona-bubble');
  const speakBtn = document.getElementById('persona-speak-btn');

  const sameImage = _lastImageUrl === imageUrl;
  const sameQuote = _lastQuote    === quote;
  if (sameImage && sameQuote) return;

  if (!sameImage && imageUrl) {
    _lastImageUrl = imageUrl;
    img.style.opacity = '0';
    img.onload = () => {
      spinner.style.display = 'none';
      img.style.display     = '';
      img.style.opacity     = '1';
    };
    img.onerror = () => { spinner.style.display = ''; };
    img.src = imageUrl + '?t=' + Date.now();
  }

  if (!sameQuote) {
    _lastQuote = quote;
    bubble.style.opacity = '0';
    setTimeout(() => {
      bubble.textContent   = quote || '';
      bubble.style.opacity = '1';
    }, 280);
  }

  if (speakBtn) speakBtn.style.display = quote ? '' : 'none';
}

// ─── TTS speak ────────────────────────────────────────────────────────────────

async function speakText(text) {
  if (!text) return;
  const btn = document.getElementById('persona-speak-btn');
  if (btn) btn.disabled = true;
  try {
    const res  = await fetch('/tts/speak', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ text }),
    });
    if (!res.ok) throw new Error(`TTS HTTP ${res.status}`);
    const blob  = await res.blob();
    const url   = URL.createObjectURL(blob);
    const audio = new Audio(url);
    audio.onended = () => { URL.revokeObjectURL(url); if (btn) btn.disabled = false; };
    audio.onerror = () => { if (btn) btn.disabled = false; };
    audio.play().catch(() => { if (btn) btn.disabled = false; });
  } catch (e) {
    console.error('[persona_desktop] TTS error:', e);
    if (btn) btn.disabled = false;
  }
}

// ─── Chat ─────────────────────────────────────────────────────────────────────

function _appendBubble(text, role, pending = false) {
  const el = document.createElement('div');
  el.className = `chat-bubble ${role}`;
  if (pending) {
    el.innerHTML = '<div class="typing-dots"><span></span><span></span><span></span></div>';
  } else {
    el.textContent = text;
  }
  const histEl = document.getElementById('chat-history');
  // Hide the empty-state hint once the first message arrives
  const hint = histEl.querySelector('.chat-empty-hint');
  if (hint) hint.style.display = 'none';
  histEl.appendChild(el);
  histEl.scrollTop = histEl.scrollHeight;
  if (role === 'user') {
    _chatMsgCount++;
    _resetAutoCollapse();
  }
  return el;
}

async function sendMessage() {
  const input   = document.getElementById('chat-input');
  const sendBtn = document.getElementById('btn-send');
  const query   = input.value.trim();
  if (!query || sendBtn.disabled) return;

  input.value      = '';
  sendBtn.disabled = true;

  _appendBubble(query, 'user');
  const pendingEl = _appendBubble('', 'persona', true);

  try {
    const res  = await fetch('/persona/chat', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ query, history: _history }),
    });
    const data  = await res.json();
    const reply = data.reply || '...';

    // Fill in the typing dots with the actual reply
    pendingEl.innerHTML  = '';
    pendingEl.textContent = reply;
    _chatMsgCount++;
    _resetAutoCollapse();

    // Record exchange in session history
    _history.push([query, reply]);

    // Update persona image if a mood-matched one came back
    // NOTE: we intentionally do NOT update the speech bubble from chat replies —
    // the ambient bubble reflects the persona's state, not the conversation.
    if (data.image_url && data.image_url !== _lastImageUrl) {
      _lastImageUrl = data.image_url;
      const img = document.getElementById('persona-img');
      img.style.opacity = '0';
      img.onload = () => { img.style.opacity = '1'; };
      img.src = data.image_url + '?t=' + Date.now();
      document.getElementById('persona-spinner').style.display = 'none';
      img.style.display = '';
    }

    // Auto-speak — only when TTS is enabled
    if (_ttsEnabled) speakText(reply);

  } catch (e) {
    console.error('[persona_desktop] chat error:', e);
    pendingEl.innerHTML  = '';
    pendingEl.textContent = 'Something went wrong.';
  } finally {
    sendBtn.disabled = false;
    input.focus();
  }
}

// ─── Boot ─────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  // Window controls
  document.getElementById('btn-close')
    ?.addEventListener('click', () => _callApi('close'));
  document.getElementById('btn-minimize')
    ?.addEventListener('click', () => _callApi('minimize'));

  // TTS toggle
  _applyTtsState();
  document.getElementById('btn-tts-toggle')
    ?.addEventListener('click', _toggleTts);

  // Chat toggle — mousedown initiates drag or click (no 'click' listener needed)
  document.getElementById('chat-toggle')
    ?.addEventListener('mousedown', e => {
      if (_fillMode) return;   // toggle is inert in fill mode
      _dragStartY    = e.clientY;
      _dragStartImgH = document.getElementById('persona-img-wrap')?.offsetHeight ?? 0;
      _dragging      = false;
    });

  // Combined mousemove: splitter drag + resize-edge cursor
  document.addEventListener('mousemove', e => {
    if (_dragStartY !== null) {
      if (!_chatOpen) return;   // collapsed → treat as click only, no drag movement
      const dy = e.clientY - _dragStartY;
      if (!_dragging && Math.abs(dy) < DRAG_THRESHOLD_PX) return;
      if (!_dragging) {
        _dragging = true;
        document.body.classList.add('dragging-split');
      }
      const avail     = _availableH();
      const newImageH = Math.max(IMAGE_MIN_H, Math.min(_dragStartImgH + dy, avail - CHAT_MIN_H));
      const newChatH  = avail - newImageH;
      // Apply heights directly during drag (no transition)
      const imgWrap   = document.getElementById('persona-img-wrap');
      const chatPanel = document.getElementById('chat-panel');
      if (imgWrap)   { imgWrap.style.flex = `0 0 ${newImageH}px`; imgWrap.style.maxHeight = ''; }
      if (chatPanel) { chatPanel.style.maxHeight = `${newChatH}px`; }
    } else {
      _setResizeCursor(e.clientX, e.clientY);
    }
  });

  // Mouseup: finalise drag (save ratio) or treat as click (toggle collapse)
  document.addEventListener('mouseup', () => {
    if (_dragStartY === null) return;
    const wasDragging = _dragging;
    _dragStartY    = null;
    _dragStartImgH = null;
    _dragging      = false;
    document.body.classList.remove('dragging-split');

    if (!wasDragging) {
      _toggleChat();
      return;
    }

    // Save new ratio anchored to current window size
    const avail  = _availableH();
    const imgH   = document.getElementById('persona-img-wrap')?.offsetHeight ?? 0;
    _splitRatio    = imgH / avail;
    _naturalTotalH = avail + 1;   // +1 so current window stays constrained; must grow to enter fill
    _callApi('setChatSplitRatio', _splitRatio);
  });

  // Re-evaluate fill vs constrained on every window resize
  window.addEventListener('resize', _applyLayout);

  // Manual ambient-quote speak (always works regardless of auto-speak toggle)
  document.getElementById('persona-speak-btn')
    ?.addEventListener('click', () => speakText(_lastQuote));

  // Chat input
  document.getElementById('btn-send')
    ?.addEventListener('click', sendMessage);
  document.getElementById('chat-input')
    ?.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    });

  // Initial layout (launcher will also call _applyPersistedSplitRatio shortly after)
  _applyLayout();
  fetchAmbient();
});
