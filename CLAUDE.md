# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

Home Pal is a home dashboard Flask server with a plain HTML/JS/CSS frontend. It runs on a local network and is designed for display on a tablet. It also controls embedded hardware: an ESP32 LED strip and an ESP32 e-ink display.

## Running the server

```bash
source .venv/Scripts/activate  # Windows/WSL
python app.py
```

Server runs at `http://0.0.0.0:5000/`. `DEBUG=True` in `config.py` so Flask auto-reloads on file changes. Starting the server also starts Ollama as a managed subprocess (see `services/ollama_service.py`).

## Installing dependencies

```bash
pip install -r requirements.txt
```

`torch` / `torchaudio` are required for Coqui TTS. `diffusers` / `transformers` / `accelerate` are required for Stable Diffusion image generation. Ollama must be installed separately as a native application (see README).

## Architecture

### Backend (Python/Flask)

- **`app.py`** — Flask app factory. Initializes Peewee DB connection lifecycle, Flask-Caching, starts Ollama subprocess, and registers routes.
- **`routes.py`** — All HTTP endpoints via `init_routes(app)`. Single place to add new routes.
- **`models.py`** — Peewee ORM models backed by SQLite (`my_database.db`). Tables: `Task`, `WeatherData`, `ShoppingListItem`, `WeatherLocation`, `SmartHomeDevice`. Auto-created on import.
- **`config.py`** — Central config: `WEATHER_LOCATION` (fallback), `TRAIN_STATION_URL`, `SECRET_KEY`.
- **`cache.py`** — Singleton `Flask-Caching` instance. **Important:** use `@cache.memoize()` for plain functions, `@cache.cached()` only for Flask view functions (views have request context; plain functions do not).

### Services (`services/`)

| File | Responsibility |
|---|---|
| `weather_service.py` | Fetches from Open-Meteo API; caches in SQLite (1h TTL). `get_default_location()` returns the is_default location or falls back to `Config.WEATHER_LOCATION`. |
| `train_scrape_service.py` | Scrapes JR East timetable HTML with BeautifulSoup; auto-selects weekday vs weekend. |
| `google_calendar.py` | Google Calendar OAuth2 Blueprint. Credentials in `token.pickle`. `get_all_events()` fetches all calendars (`@cache.memoize` 1h). |
| `tts_service.py` | Coqui TTS (`tts_models/en/vctk/vits`), initialized once at module load. Returns WAV as BytesIO. |
| `image_gen_service.py` | Stable Diffusion (`Lykon/dreamshaper-8`). Lazy-loads pipeline on first call. Caches images at `tmp/persona/{state}.png`. Uses `_in_progress: set[str]` to prevent duplicate generation threads. Fixed seed 42 for character consistency. |
| `ollama_service.py` | Starts `ollama serve` as a managed subprocess, waits for it to be ready, then pulls `OLLAMA_MODEL` if not already downloaded. Defines `OLLAMA_BASE_URL` and `OLLAMA_MODEL` — import these rather than redefining them. |
| `image_dither.py` | Dithers dashboard screenshot to 1-bit BMP for the e-ink display. |
| `smart_home_service.py` | CRUD for `SmartHomeDevice`. The `led` device is enriched by `LedEnricherService` before returning. |

### Agents (`agents/`)

- **`agent_service.py`** — Keyword-based intent router. Dispatches to `WeatherAgentService`, `CalendarAgentService`, or smart home actions.
- **`persona_agent.py`** — Classifies current weather + time of day into a state key (`{weather}_{period}` or `in_meeting` / `meeting_soon`). Calls Ollama for dynamic quotes with a 10-minute TTL cache. Calendar overrides take highest priority.
- `weather_agent_service.py` / `calendar_agent_service.py` — Wrap data services for natural-language TTS responses.

#### Persona state system

State key format: `{weather_key}_{time_period}` e.g. `cold_evening`, `heavy_rain_morning`.
Calendar overrides (`in_meeting`, `meeting_soon`) skip the weather+time logic entirely.
`CHARACTER_PREFIX` in `persona_agent.py` + fixed seed 42 are the SD consistency anchors — changing either requires deleting all cached images in `tmp/persona/`.

### Smart home (`smart_home/`)

- `led_enricher_service.py` — Builds LED indicator payload for ESP32. Merges indicators by priority: weather (0) < occasions (1) < alerts (2) < calendar (3).

### Frontend (`frontend/`)

- Single page: `frontend/templates/index.html`.
- Each widget has its own JS file in `frontend/static/js/`.
- Persona widget is `position: fixed` outside the grid (bottom-left), rendered by `persona.js` + `agent.css`.

### Embedded (`embedded/`)

- **`led_control_wifi/`** — ESP32 sketch. Polls `GET /sh/led`, renders JSON indicator payloads to a 60-LED NeoPixel strip. FreeRTOS + BLE presence tracker.
- **`display_screen/`** — ESP32 e-ink sketch. Fetches `GET /image.bin` (dithered BMP). Uses Zigbee for sensor reporting.
- Secrets go in `embedded/*/secrets.h` (not committed).

## Key conventions

- All new routes belong in `routes.py` inside `init_routes(app)`.
- New DB models go in `models.py`; add to the `create_tables([...])` call at the bottom.
- Use `@cache.memoize()` (not `@cache.cached`) for any function that is not a Flask view.
- Import `OLLAMA_BASE_URL` and `OLLAMA_MODEL` from `services/ollama_service.py` — do not redefine them.
- To force persona image regeneration: delete `tmp/persona/` and refresh the dashboard.
