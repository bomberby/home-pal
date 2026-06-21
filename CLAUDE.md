# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

Home Pal is a home dashboard Flask server with a plain HTML/JS/CSS frontend. It runs on a local network and is designed for display on a tablet. It also controls embedded hardware: an ESP32 LED strip and an ESP32 e-ink display.

## Running the server

```bash
source .venv/Scripts/activate  # Windows/WSL
python app.py
```

Server runs at `http://0.0.0.0:5000/`. `DEBUG=True` in `config.py` so Flask auto-reloads on file changes. Starting the server also starts Ollama as a managed subprocess (see `agents/llm/ollama_service.py`).

## Installing dependencies

**torch must be installed first with the correct CUDA index** (GPU is GTX 1070, SM 6.1/Pascal — torch 2.4+ drops SM 6.1 support):

```bash
pip install torch==2.3.1 torchaudio==2.3.1 torchvision==0.18.1 --index-url https://download.pytorch.org/whl/cu121
```

Then install the rest:

```bash
pip install -r requirements.txt
```

`diffusers` / `transformers` / `accelerate` are required for Stable Diffusion image generation. Ollama must be installed separately as a native application (see README).

## Architecture

### Backend (Python/Flask)

- **`app.py`** — Flask app factory. Initializes Peewee DB connection lifecycle, Flask-Caching, starts Ollama subprocess, and registers routes.
- **`routes/__init__.py`** — Registers all Flask Blueprints via `init_routes(app)`. Blueprint modules live in `routes/<area>.py`.
- **`models.py`** — Peewee ORM models backed by SQLite (`my_database.db`). Tables: `Task`, `WeatherData`, `ShoppingListItem`, `WeatherLocation`, `SmartHomeDevice`. Auto-created on import.
- **`config.py`** — Central config: `WEATHER_LOCATION` (fallback), `TRAIN_STATION_URL`, `SECRET_KEY`.
- **`cache.py`** — Singleton `Flask-Caching` instance. **Important:** use `@cache.memoize()` for plain functions, `@cache.cached()` only for Flask view functions (views have request context; plain functions do not).

### Services (`services/`)

| File | Responsibility |
|---|---|
| `weather_service.py` | Fetches from Open-Meteo API; caches in SQLite (1h TTL). `get_default_location()` returns the is_default location or falls back to `Config.WEATHER_LOCATION`. |
| `train_scrape_service.py` | Scrapes JR East timetable HTML with BeautifulSoup; auto-selects weekday vs weekend. |
| `google_calendar.py` | Google Calendar OAuth2 Blueprint. Credentials in `token.pickle`. `get_all_events()` fetches all calendars (`@cache.memoize` 1h). |
| `calendar_utils.py` | Shared calendar helpers: `parse_dt(iso_string)` (handles Z suffix), `event_date(event)`, `is_event_on(event, date_str)`. Use `parse_dt` instead of inline `fromisoformat(s.replace('Z', '+00:00'))`. |
| `notification_service.py` | Presence callbacks, air quality monitor, scheduler (morning briefing, rain warning, meeting reminder). |
| `telegram_service.py` | Bot setup, polling, `send_message`, `get_image_for_text`, `handle_text`, `handle_callback`. |

### Agents (`agents/`)

- **`agent_service.py`** — Keyword-based intent router. Dispatches to `WeatherAgentService`, `CalendarAgentService`, or smart home actions.
- **`persona/agent.py`** — Determines persona state from context. Calls LM Studio for dynamic quotes (10-min TTL). Two call styles: `_generate_quote` (short reactive, ≤10 words) and `_generate_briefing` (spoken welcome, 2 sentences, always includes current time).
- **`persona/states.py`** — All static SD prompts, fallback quotes, holiday patterns, and situation labels.
- **`llm/ollama_service.py`** — Starts `ollama serve` as a managed subprocess, waits for it to be ready, then pulls `OLLAMA_MODEL` if not already downloaded. Defines `OLLAMA_BASE_URL` and `OLLAMA_MODEL` — import these rather than redefining them.
- **`llm/lmstudio_service.py`** — Alternative LLM backend via LM Studio's OpenAI-compatible API (`localhost:1234`). Defines `LM_STUDIO_BASE_URL` and `LM_STUDIO_MODEL`.
- **`tts_service.py`** — Kokoro TTS (active backend). Lazily initialised. `TTS_BACKEND` constant at top switches between `'kokoro'` and `'coqui'`. Returns WAV as BytesIO.
- **`image/image_gen_service.py`** — Stable Diffusion (`Lykon/dreamshaper-8`). Lazy-loads pipeline on first call. Caches images at `tmp/persona/{state}.png`. Uses `_in_progress: set[str]` to prevent duplicate generation threads. Fixed seed 42 for character consistency.
- **`image/gpu_lock.py`** — Full GPU coordination API. Owns all coordination file paths; provides `gpu_lock()`, `claim_gpu()`, `write/clear_priority()`, `signal_worker()`. Import from here — never redefine paths elsewhere.
- **`image/hq_gen_worker.py`** — Subprocess worker for queued HQ/UHQ image generation jobs.
- `weather_agent_service.py` / `calendar_agent_service.py` — Wrap data services for natural-language TTS responses.
- `memory_service.py`, `reminder_service.py`, `spotify_service.py`, `chat_service.py` — Supporting AI/integrations.

#### Persona state system

Priority chain (highest first): `hub_offline` → `absent` → `welcome_{period}` → `poor_air` → `indoor_discomfort` → holiday → `in_meeting` / `meeting_soon` → `{weather}_{period}`.

State key format: `{weather}_{time_period}_{mood}` e.g. `cold_evening_tired`; `welcome_{time_period}_{mood}`; `{override}_{mood}` for fixed states. Mood is resolved by `_get_mood(state_data, period)` from `mood_overrides[period]` → `mood` → `"content"`.
`CHARACTER_PREFIX` in `agents/persona/states.py` + fixed seed 42 are the SD consistency anchors — changing either requires deleting all cached images in `tmp/persona/`.
`_make_response()` is the single builder for all persona state responses — mood, state key, prompt, quote, and suggestion all flow through it.

MQTT presence + air quality: `smart_home/home_context_service.py`. Config in `config.py`; credentials in `env/secrets/mqtt.json`.

### Smart home (`smart_home/`)

- `smart_home_service.py` — Device state management and smart home control.
- `led_enricher_service.py` — Builds LED indicator payload for ESP32. Merges indicators by priority: weather (0) < occasions (1) < alerts (2) < calendar (3).
- `home_context_service.py` — MQTT client for presence (BLE RSSI) and air quality (VOC, NOx, temp, humidity). Drives `is_home()`, `is_just_arrived()`, `has_poor_air()`, `indoor_discomfort()`.
- `image_dither.py` — Dithers dashboard screenshot to 1-bit BMP for the e-ink display.
- `daily_screen_service.py` — Generates and sends the daily e-ink screen image.

### Frontend (`frontend/`)

- Single page: `frontend/templates/index.html`.
- Each widget has its own JS file in `frontend/static/js/`.
- Persona widget is `position: fixed` outside the grid (bottom-left), rendered by `persona.js` + `agent.css`.

### Embedded (`embedded/`)

- **`led_control_wifi/`** — ESP32 sketch. Polls `GET /sh/led`, renders JSON indicator payloads to a 60-LED NeoPixel strip. FreeRTOS + BLE presence tracker.
- **`display_screen/`** — ESP32 e-ink sketch. Fetches `GET /image.bin` (dithered BMP). Uses Zigbee for sensor reporting.
- Secrets go in `embedded/*/secrets.h` (not committed).

## Key conventions

- Routes live as Flask Blueprints in `routes/<area>.py`. Register new blueprints in `routes/__init__.py` inside `init_routes(app)`.
- New DB models go in `models.py`; add to the `create_tables([...])` call at the bottom.
- Use `@cache.memoize()` (not `@cache.cached`) for any function that is not a Flask view.
- Import `OLLAMA_BASE_URL` and `OLLAMA_MODEL` from `agents/llm/ollama_service.py` — do not redefine them.
- Use `parse_dt()` from `services/calendar_utils.py` for all ISO datetime parsing — never write `fromisoformat(s.replace('Z', '+00:00'))` inline.
- One-time DB migrations (`ALTER TABLE`) must be removed from `models.py` after they have run. They are not idempotent in intent, only in effect.
- To force persona image regeneration: delete `tmp/persona/` and refresh the dashboard.
